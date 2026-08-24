import json
import pytest
from app.config import settings
from app.core.storage import (
    SupabaseStorageProvider,
    R2StorageProvider,
    LocalStorageProvider,
    StorageRepository,
)


class MockHttpxResponse:
    def __init__(self, status_code=200, content=b"", json_data=None):
        self.status_code = status_code
        self.content = content
        self._json_data = json_data
        self.text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else str(content)

    def json(self):
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


class MockHttpxClient:
    """Mock client cho Supabase Storage REST API."""

    def __init__(self, *args, **kwargs):
        self.storage = {}
        self.is_closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def close(self):
        self.is_closed = True

    def post(self, url, headers=None, content=None, json=None):
        # 1. Object upload: /storage/v1/object/{bucket}/{key}
        if "/storage/v1/object/list/" in url:
            prefix = (json or {}).get("prefix", "")
            items = []
            for k in self.storage.keys():
                if not prefix or k.startswith(prefix):
                    rel = k[len(prefix):].lstrip("/") if prefix else k
                    parts = rel.split("/")
                    name = parts[0]
                    if len(parts) > 1:
                        # Folder
                        items.append({"name": name, "id": None, "metadata": None})
                    else:
                        items.append({"name": name, "id": "file-id", "metadata": {"size": len(self.storage[k])}})
            return MockHttpxResponse(200, json_data=items)

        if "/storage/v1/object/" in url:
            parts = url.split("/storage/v1/object/")[1].split("/", 1)
            if len(parts) == 2:
                bucket, key = parts
                self.storage[key] = content if content is not None else b""
                return MockHttpxResponse(200, json_data={"Key": f"{bucket}/{key}"})

        return MockHttpxResponse(404, b"Not found")

    def get(self, url, headers=None):
        for pattern in ("/storage/v1/object/authenticated/", "/storage/v1/object/public/", "/storage/v1/object/info/authenticated/"):
            if pattern in url:
                parts = url.split(pattern)[1].split("/", 1)
                if len(parts) == 2:
                    bucket, key = parts
                    if key in self.storage:
                        return MockHttpxResponse(200, content=self.storage[key])
                    return MockHttpxResponse(404, b"Not found")
        return MockHttpxResponse(404, b"Not found")

    def request(self, method, url, headers=None, json=None):
        if method == "DELETE" and "/storage/v1/object/" in url:
            prefixes = (json or {}).get("prefixes", [])
            for p in prefixes:
                self.storage.pop(p, None)
            return MockHttpxResponse(200, json_data=[{"name": p} for p in prefixes])
        return MockHttpxResponse(404, b"Not found")


def test_supabase_provider_basic_crud(monkeypatch):
    mock_client = MockHttpxClient()
    monkeypatch.setattr("httpx.Client", lambda **kwargs: mock_client)

    provider = SupabaseStorageProvider(
        base_url="https://xyz.supabase.co",
        api_key="test-key",
        bucket="novels",
    )

    assert provider.is_active is True
    assert provider.provider_name == "supabase"

    # 1. Put bytes
    pub_url = provider.put_bytes("test/hello.txt", b"Hello Supabase!", content_type="text/plain")
    assert pub_url == "https://xyz.supabase.co/storage/v1/object/public/novels/test/hello.txt"
    assert "test/hello.txt" in mock_client.storage

    # 2. Get bytes
    data = provider.get_bytes("test/hello.txt")
    assert data == b"Hello Supabase!"

    # 3. File exists
    assert provider.file_exists("test/hello.txt") is True
    assert provider.file_exists("test/nonexistent.txt") is False

    # 4. JSON put & get
    json_payload = {"title": "Test Novel", "chapters": 10}
    ok = provider.put_json("novels/novel-1/metadata.json", json_payload)
    assert ok is True

    loaded_json = provider.get_json("novels/novel-1/metadata.json")
    assert loaded_json == json_payload

    # 5. List files
    files = provider.list_files("novels/")
    assert "novels/novel-1/metadata.json" in files

    # 6. Delete file
    deleted = provider.delete_file("test/hello.txt")
    assert deleted is True
    assert "test/hello.txt" not in mock_client.storage


def test_storage_repository_provider_switching(monkeypatch):
    mock_supabase_client = MockHttpxClient()
    monkeypatch.setattr("httpx.Client", lambda **kwargs: mock_supabase_client)

    # 1. Test Supabase active
    settings.storage_provider = "supabase"
    settings.supabase_url = "https://xyz.supabase.co"
    settings.supabase_key = "test-key"
    settings.supabase_storage_bucket = "novels"

    repo = StorageRepository()
    assert repo.active_provider_name == "supabase"
    assert repo.is_supabase_active is True
    assert repo.is_blob_active is True

    # Upload using unified interface
    repo.put_bytes("test/unified.txt", b"Unified content")
    assert repo.get_bytes("test/unified.txt") == b"Unified content"

    # Backward compatibility alias
    assert repo.file_exists_in_r2("test/unified.txt") is True

    # 2. Test R2 active
    settings.storage_provider = "r2"
    settings.cloudflare_account_id = "test-acc"
    settings.cloudflare_r2_access_key_id = "key-id"
    settings.cloudflare_r2_secret_access_key = "secret"
    settings.cloudflare_r2_bucket_name = "test-bucket"

    from tests.test_storage_repository import FakeR2Client
    repo_r2 = StorageRepository()
    repo_r2.r2_client = FakeR2Client()

    assert repo_r2.active_provider_name == "r2"
    assert repo_r2.is_r2_active is True

    repo_r2.put_bytes("test/r2_file.txt", b"R2 content")
    assert repo_r2.get_bytes("test/r2_file.txt") == b"R2 content"


def test_supabase_provider_client_pooling_and_close(monkeypatch):
    clients_created = []

    def mock_client_factory(**kwargs):
        client = MockHttpxClient(**kwargs)
        clients_created.append(client)
        return client

    monkeypatch.setattr("httpx.Client", mock_client_factory)

    provider = SupabaseStorageProvider(
        base_url="https://xyz.supabase.co",
        api_key="test-key",
        bucket="novels",
    )

    # Calling put_bytes multiple times should reuse the same httpx.Client instance
    for i in range(10):
        provider.put_bytes(f"chapters/ch_{i}.txt", f"Chapter {i} text".encode("utf-8"))

    assert len(clients_created) == 1

    # Closing provider cleans up client
    provider.close()
    assert provider._client is None
