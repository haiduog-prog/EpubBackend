import concurrent.futures
import json
import logging
import mimetypes
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import boto3
from botocore.config import Config
import httpx

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    firebase_admin = None
    credentials = None
    firestore = None

from app.config import settings
from app.db.session import db_session
from app.modules.book_bible.persistence.repository import BookBibleRepository
from app.modules.library.persistence.repository import LibraryRepository
from app.schemas.book_bible import BookBible, BookBibleDelta
from app.schemas.translation import TranslationJob
from app.modules.book_bible.application.facade import BookBibleService

logger = logging.getLogger("EpubBackend.StorageRepository")


class BaseStorageProvider:
    """Interface định nghĩa các thao tác lưu trữ Blob chuẩn."""

    @property
    def is_active(self) -> bool:
        return False

    @property
    def provider_name(self) -> str:
        return "base"

    def put_bytes(self, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> Optional[str]:
        raise NotImplementedError

    def get_bytes(self, object_name: str, raise_on_error: bool = False) -> Optional[bytes]:
        raise NotImplementedError

    def upload_file(self, file_path: str, object_name: str, content_type: Optional[str] = None) -> Optional[str]:
        raise NotImplementedError

    def put_json(self, object_name: str, data: dict) -> bool:
        raise NotImplementedError

    def get_json(self, object_name: str, raise_on_error: bool = False) -> Optional[dict]:
        raise NotImplementedError

    def list_json_objects(self, prefix: str) -> List[dict]:
        raise NotImplementedError

    def file_exists(self, object_name: str) -> bool:
        raise NotImplementedError

    def delete_file(self, object_name: str) -> bool:
        raise NotImplementedError

    def delete_files(self, object_names: List[str]) -> int:
        raise NotImplementedError

    def list_files(self, prefix: str = "", raise_on_error: bool = False) -> List[str]:
        raise NotImplementedError

    def get_public_url(self, object_name: str) -> str:
        raise NotImplementedError


class SupabaseStorageProvider(BaseStorageProvider):
    """Lưu trữ Blob trực tiếp trên Supabase Storage thông qua REST API & CDN."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        bucket: Optional[str] = None,
        public_url: Optional[str] = None,
    ):
        self.base_url = (base_url if base_url is not None else settings.supabase_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.supabase_key
        self.bucket = bucket if bucket is not None else settings.supabase_storage_bucket
        self.public_url = (public_url if public_url is not None else settings.supabase_storage_public_url).rstrip("/")

    @property
    def provider_name(self) -> str:
        return "supabase"

    @property
    def is_active(self) -> bool:
        return bool(self.base_url and self.api_key and self.bucket)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
        }

    def get_public_url(self, object_name: str) -> str:
        clean_key = object_name.lstrip("/")
        if self.public_url:
            return f"{self.public_url}/{clean_key}"
        if self.base_url and self.bucket:
            return f"{self.base_url}/storage/v1/object/public/{self.bucket}/{clean_key}"
        return f"/storage/{clean_key}"

    def put_bytes(self, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> Optional[str]:
        if not self.is_active:
            return None
        clean_key = object_name.lstrip("/")
        url = f"{self.base_url}/storage/v1/object/{self.bucket}/{clean_key}"
        headers = self._get_headers()
        headers["Content-Type"] = content_type
        headers["x-upsert"] = "true"

        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, headers=headers, content=data)
                if resp.status_code in (200, 201):
                    return self.get_public_url(clean_key)
                logger.warning("Supabase put_bytes failed (%s, status=%d): %s", clean_key, resp.status_code, resp.text)
                return None
        except Exception as exc:
            logger.warning("Supabase put_bytes exception (%s): %s", clean_key, exc)
            return None

    def upload_file(self, file_path: str, object_name: str, content_type: Optional[str] = None) -> Optional[str]:
        if not self.is_active or not os.path.exists(file_path):
            return None
        guessed_type, _ = mimetypes.guess_type(file_path)
        actual_type = content_type or guessed_type or "application/octet-stream"
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            return self.put_bytes(object_name, data, content_type=actual_type)
        except Exception as exc:
            logger.error("Failed to upload file to Supabase Storage (%s): %s", object_name, exc)
            return None

    def get_bytes(self, object_name: str, raise_on_error: bool = False) -> Optional[bytes]:
        if not self.is_active:
            if raise_on_error:
                raise RuntimeError("Supabase storage is not active or bucket not configured")
            return None
        clean_key = object_name.lstrip("/")
        url = f"{self.base_url}/storage/v1/object/authenticated/{self.bucket}/{clean_key}"
        headers = self._get_headers()

        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.content
                if resp.status_code == 404:
                    # Fallback sang public URL endpoint nếu có
                    pub_url = f"{self.base_url}/storage/v1/object/public/{self.bucket}/{clean_key}"
                    pub_resp = client.get(pub_url)
                    if pub_resp.status_code == 200:
                        return pub_resp.content
                    return None
                if raise_on_error:
                    resp.raise_for_status()
                logger.warning("Supabase get_bytes failed (%s, status=%d): %s", clean_key, resp.status_code, resp.text)
                return None
        except Exception as exc:
            if raise_on_error:
                raise exc
            logger.warning("Supabase get_bytes exception (%s): %s", clean_key, exc)
            return None

    def put_json(self, object_name: str, data: dict) -> bool:
        if not self.is_active:
            return False
        try:
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            url = self.put_bytes(object_name, body, content_type="application/json; charset=utf-8")
            return url is not None
        except Exception as exc:
            logger.warning("Failed to put JSON to Supabase Storage (%s): %s", object_name, exc)
            return False

    def get_json(self, object_name: str, raise_on_error: bool = False) -> Optional[dict]:
        data = self.get_bytes(object_name, raise_on_error=raise_on_error)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("Corrupted JSON in Supabase Storage (%s): %s", object_name, exc)
            return None

    def file_exists(self, object_name: str) -> bool:
        if not self.is_active:
            return False
        clean_key = object_name.lstrip("/")
        url = f"{self.base_url}/storage/v1/object/authenticated/{self.bucket}/{clean_key}"
        headers = self._get_headers()
        headers["Range"] = "bytes=0-0"
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code in (200, 206):
                    return True
                if resp.status_code == 404:
                    return False
                info_url = f"{self.base_url}/storage/v1/object/info/authenticated/{self.bucket}/{clean_key}"
                info_resp = client.get(info_url, headers=self._get_headers())
                return info_resp.status_code == 200
        except Exception:
            return False

    def delete_file(self, object_name: str) -> bool:
        count = self.delete_files([object_name])
        return count > 0

    def delete_files(self, object_names: List[str]) -> int:
        if not self.is_active or not object_names:
            return 0
        url = f"{self.base_url}/storage/v1/object/{self.bucket}"
        headers = self._get_headers()
        headers["Content-Type"] = "application/json"
        deleted_count = 0

        with httpx.Client(timeout=30.0) as client:
            for i in range(0, len(object_names), 100):
                chunk = [k.lstrip("/") for k in object_names[i : i + 100]]
                try:
                    resp = client.request("DELETE", url, headers=headers, json={"prefixes": chunk})
                    if resp.status_code in (200, 204):
                        deleted_count += len(chunk)
                    else:
                        logger.warning("Supabase delete_files failed (status=%d): %s", resp.status_code, resp.text)
                except Exception as exc:
                    logger.warning("Supabase delete_files exception: %s", exc)
        return deleted_count

    def list_files(self, prefix: str = "", raise_on_error: bool = False) -> List[str]:
        if not self.is_active:
            return []
        results = []
        clean_prefix = prefix.strip("/")
        queue = [clean_prefix] if clean_prefix else [""]
        headers = self._get_headers()
        url = f"{self.base_url}/storage/v1/object/list/{self.bucket}"

        with httpx.Client(timeout=30.0) as client:
            while queue:
                curr = queue.pop(0)
                offset = 0
                limit = 100
                while True:
                    payload = {
                        "prefix": curr,
                        "limit": limit,
                        "offset": offset,
                        "sortBy": {"column": "name", "order": "asc"},
                    }
                    try:
                        resp = client.post(url, headers=headers, json=payload)
                        if resp.status_code != 200:
                            if raise_on_error:
                                resp.raise_for_status()
                            logger.warning("Supabase list error (prefix=%s, status=%d): %s", curr, resp.status_code, resp.text)
                            break
                        items = resp.json()
                        if not items:
                            break
                        for item in items:
                            name = item.get("name", "")
                            full_key = f"{curr}/{name}".strip("/") if curr else name
                            if item.get("id") is None and item.get("metadata") is None:
                                queue.append(full_key)
                            else:
                                results.append(full_key)
                        if len(items) < limit:
                            break
                        offset += limit
                    except Exception as exc:
                        logger.warning("Supabase list exception (prefix=%s): %s", curr, exc)
                        if raise_on_error:
                            raise exc
                        break
        return sorted(list(set(results)))

    def list_json_objects(self, prefix: str) -> List[dict]:
        files = self.list_files(prefix)
        json_files = [f for f in files if f.endswith(".json")]
        if not json_files:
            return []
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(self.get_json, f) for f in json_files]
            for fut in concurrent.futures.as_completed(futures):
                data = fut.result()
                if data is not None:
                    results.append(data)
        return results


class R2StorageProvider(BaseStorageProvider):
    """Lưu trữ Blob trên Cloudflare R2 (S3 API). Giữ nguyên 100% logic để dễ dàng chuyển lại."""

    def __init__(
        self,
        account_id: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        bucket_name: Optional[str] = None,
        public_url: Optional[str] = None,
    ):
        self.account_id = account_id if account_id is not None else settings.cloudflare_account_id
        self.access_key_id = access_key_id if access_key_id is not None else settings.cloudflare_r2_access_key_id
        self.secret_access_key = secret_access_key if secret_access_key is not None else settings.cloudflare_r2_secret_access_key
        self.bucket_name = bucket_name if bucket_name is not None else settings.cloudflare_r2_bucket_name
        self.public_url = (public_url if public_url is not None else settings.cloudflare_r2_public_url).rstrip("/")
        self.r2_client = None
        self.r2_enabled = False
        self._init_client()

    def _init_client(self):
        if self.account_id and self.access_key_id and self.secret_access_key:
            try:
                endpoint_url = f"https://{self.account_id}.r2.cloudflarestorage.com"
                self.r2_client = boto3.client(
                    "s3",
                    endpoint_url=endpoint_url,
                    aws_access_key_id=self.access_key_id,
                    aws_secret_access_key=self.secret_access_key,
                    config=Config(signature_version="s3v4", max_pool_connections=50),
                    region_name="auto",
                )
                self.r2_enabled = True
            except Exception as exc:
                logger.warning("Cloudflare R2 init skipped: %s", exc)

    @property
    def provider_name(self) -> str:
        return "r2"

    @property
    def is_active(self) -> bool:
        return bool(self.r2_enabled and self.r2_client and self.bucket_name)

    def get_public_url(self, object_name: str) -> str:
        clean_key = object_name.lstrip("/")
        if self.public_url:
            return f"{self.public_url}/{clean_key}"
        if self.account_id and self.bucket_name:
            return f"https://{self.account_id}.r2.cloudflarestorage.com/{self.bucket_name}/{clean_key}"
        return f"/storage/{clean_key}"

    def put_bytes(self, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> Optional[str]:
        if not self.is_active:
            return None
        clean_key = object_name.lstrip("/")
        try:
            self.r2_client.put_object(
                Bucket=self.bucket_name,
                Key=clean_key,
                Body=data,
                ContentType=content_type,
            )
            return self.get_public_url(clean_key)
        except Exception as exc:
            logger.warning("Failed to put bytes to Cloudflare R2 (%s): %s", clean_key, exc)
            return None

    def upload_file(self, file_path: str, object_name: str, content_type: Optional[str] = None) -> Optional[str]:
        if not self.is_active or not os.path.exists(file_path):
            return None
        clean_key = object_name.lstrip("/")
        try:
            extra_args = {}
            if content_type:
                extra_args["ContentType"] = content_type
            self.r2_client.upload_file(
                file_path, self.bucket_name, clean_key, ExtraArgs=extra_args if extra_args else None
            )
            return self.get_public_url(clean_key)
        except Exception as exc:
            logger.error("Failed to upload file to Cloudflare R2 (%s): %s", clean_key, exc)
            return None

    def get_bytes(self, object_name: str, raise_on_error: bool = False) -> Optional[bytes]:
        if not self.is_active:
            if raise_on_error:
                raise RuntimeError("Cloudflare R2 is not active")
            return None
        clean_key = object_name.lstrip("/")
        try:
            response = self.r2_client.get_object(
                Bucket=self.bucket_name,
                Key=clean_key,
            )
            return response["Body"].read()
        except Exception as exc:
            err_code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if err_code not in ("NoSuchKey", "404"):
                logger.warning("Failed to get bytes from Cloudflare R2 (%s): %s", clean_key, exc)
                if raise_on_error:
                    raise exc
            return None

    def put_json(self, object_name: str, data: dict) -> bool:
        if not self.is_active:
            return False
        clean_key = object_name.lstrip("/")
        try:
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.r2_client.put_object(
                Bucket=self.bucket_name,
                Key=clean_key,
                Body=body,
                ContentType="application/json; charset=utf-8",
            )
            return True
        except Exception as exc:
            logger.warning("Failed to put JSON to Cloudflare R2 (%s): %s", clean_key, exc)
            return False

    def get_json(self, object_name: str, raise_on_error: bool = False) -> Optional[dict]:
        data = self.get_bytes(object_name, raise_on_error=raise_on_error)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("Corrupted JSON in Cloudflare R2 (%s): %s", object_name, exc)
            return None

    def file_exists(self, object_name: str) -> bool:
        if not self.is_active:
            return False
        clean_key = object_name.lstrip("/")
        try:
            self.r2_client.head_object(
                Bucket=self.bucket_name,
                Key=clean_key,
            )
            return True
        except Exception:
            return False

    def delete_file(self, object_name: str) -> bool:
        if not self.is_active:
            return False
        clean_key = object_name.lstrip("/")
        try:
            self.r2_client.delete_object(
                Bucket=self.bucket_name,
                Key=clean_key,
            )
            return True
        except Exception as exc:
            logger.warning("Failed to delete object %s from R2: %s", clean_key, exc)
            return False

    def delete_files(self, object_names: List[str]) -> int:
        if not self.is_active or not object_names:
            return 0
        deleted_count = 0
        for i in range(0, len(object_names), 500):
            chunk = [{"Key": k.lstrip("/")} for k in object_names[i : i + 500]]
            try:
                self.r2_client.delete_objects(
                    Bucket=self.bucket_name,
                    Delete={"Objects": chunk},
                )
                deleted_count += len(chunk)
            except Exception as exc:
                logger.warning("Failed to batch delete objects from R2: %s", exc)
        return deleted_count

    def list_files(self, prefix: str = "", raise_on_error: bool = False) -> List[str]:
        if not self.is_active:
            return []
        keys = set()
        clean_prefix = prefix.strip("/")
        try:
            paginator = self.r2_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=clean_prefix):
                for item in page.get("Contents", []):
                    k = item.get("Key", "")
                    if k:
                        keys.add(k)
        except Exception as exc:
            logger.error("Failed to list R2 files with prefix %s: %s", clean_prefix, exc)
            if raise_on_error:
                raise exc
        return sorted(list(keys))

    def list_json_objects(self, prefix: str) -> List[dict]:
        if not self.is_active:
            return []
        clean_prefix = prefix.strip("/")
        try:
            paginator = self.r2_client.get_paginator("list_objects_v2")
            keys = []
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=clean_prefix):
                for item in page.get("Contents", []):
                    key = item.get("Key", "")
                    if key.endswith(".json"):
                        keys.append(key)
            if not keys:
                return []
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                futures = [executor.submit(self.get_json, k) for k in keys]
                for fut in concurrent.futures.as_completed(futures):
                    data = fut.result()
                    if data is not None:
                        results.append(data)
            return results
        except Exception as exc:
            logger.warning("Failed to list JSON objects from Cloudflare R2 (prefix=%s): %s", clean_prefix, exc)
            return []


class LocalStorageProvider(BaseStorageProvider):
    """Fallback lưu trữ trên ổ đĩa cục bộ (thư mục storage/)."""

    def __init__(self, base_dir: str = "storage"):
        self.base_dir = base_dir

    @property
    def provider_name(self) -> str:
        return "local"

    @property
    def is_active(self) -> bool:
        return True

    def get_public_url(self, object_name: str) -> str:
        return f"/storage/{object_name.lstrip('/')}"

    def put_bytes(self, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> Optional[str]:
        path = os.path.join(self.base_dir, object_name.lstrip("/"))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            return self.get_public_url(object_name)
        except Exception as exc:
            logger.warning("Failed to write local file %s: %s", path, exc)
            return None

    def upload_file(self, file_path: str, object_name: str, content_type: Optional[str] = None) -> Optional[str]:
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            return self.put_bytes(object_name, data)
        except Exception as exc:
            logger.warning("Failed to copy local file %s: %s", file_path, exc)
            return None

    def get_bytes(self, object_name: str, raise_on_error: bool = False) -> Optional[bytes]:
        paths = [
            os.path.join(self.base_dir, object_name.lstrip("/")),
            object_name,
        ]
        for p in paths:
            if os.path.exists(p) and os.path.isfile(p):
                try:
                    with open(p, "rb") as f:
                        return f.read()
                except Exception as exc:
                    if raise_on_error:
                        raise exc
        if raise_on_error:
            raise FileNotFoundError(f"Local file not found: {object_name}")
        return None

    def put_json(self, object_name: str, data: dict) -> bool:
        path = os.path.join(self.base_dir, object_name.lstrip("/"))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.warning("Failed to write local JSON %s: %s", path, exc)
            return False

    def get_json(self, object_name: str, raise_on_error: bool = False) -> Optional[dict]:
        data = self.get_bytes(object_name, raise_on_error=raise_on_error)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("Corrupted local JSON %s: %s", object_name, exc)
            return None

    def file_exists(self, object_name: str) -> bool:
        path1 = os.path.join(self.base_dir, object_name.lstrip("/"))
        return os.path.exists(path1) or os.path.exists(object_name)

    def delete_file(self, object_name: str) -> bool:
        success = False
        paths = [os.path.join(self.base_dir, object_name.lstrip("/")), object_name]
        for p in paths:
            if os.path.exists(p) and os.path.isfile(p):
                try:
                    os.remove(p)
                    success = True
                except Exception as exc:
                    logger.warning("Failed to delete local file %s: %s", p, exc)
        return success

    def delete_files(self, object_names: List[str]) -> int:
        count = 0
        for name in object_names:
            if self.delete_file(name):
                count += 1
        return count

    def list_files(self, prefix: str = "", raise_on_error: bool = False) -> List[str]:
        keys = set()
        clean_prefix = prefix.strip("/")
        if os.path.exists(self.base_dir):
            for root, _, files in os.walk(self.base_dir):
                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, self.base_dir).replace("\\", "/")
                    if not clean_prefix or rel.startswith(clean_prefix):
                        keys.add(rel)
        if os.path.exists("data"):
            for root, _, files in os.walk("data"):
                for f in files:
                    full = os.path.join(root, f)
                    rel = full.replace("\\", "/")
                    if not clean_prefix or rel.startswith(clean_prefix):
                        keys.add(rel)
        return sorted(list(keys))

    def list_json_objects(self, prefix: str) -> List[dict]:
        files = self.list_files(prefix)
        json_files = [f for f in files if f.endswith(".json")]
        results = []
        for jf in json_files:
            data = self.get_json(jf)
            if data is not None:
                results.append(data)
        return results


class StorageRepository:
    """Quản trị lưu trữ tổng thể: Database entities, Translation Jobs, Book Bibles & Pluggable Blob Storage."""

    def __init__(self):
        self._jobs: Dict[str, TranslationJob] = {}
        self._bibles: Dict[str, BookBible] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.RLock()
        self.firebase_enabled = False
        self.firestore_db = None

        # Khởi tạo các Blob Providers
        self.supabase_provider = SupabaseStorageProvider()
        self.r2_provider = R2StorageProvider()
        self.local_provider = LocalStorageProvider()

        # Firebase setup
        self._init_firebase()

    def _init_firebase(self):
        if not settings.firebase_enabled:
            return
        cred_path = settings.firebase_service_account_key
        cred_json_str = settings.firebase_credentials_json
        try:
            cred = None
            resolved_cred_path = cred_path
            if not os.path.isabs(resolved_cred_path):
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
                resolved_cred_path = os.path.join(project_root, resolved_cred_path)
            if os.path.exists(resolved_cred_path):
                cred = credentials.Certificate(resolved_cred_path)
            elif cred_json_str:
                cred = credentials.Certificate(json.loads(cred_json_str))
            if cred:
                if not firebase_admin._apps:
                    firebase_admin.initialize_app(cred)
                self.firestore_db = firestore.client()
                self.firebase_enabled = True
        except Exception as exc:
            logger.warning("Firebase init skipped: %s", exc)

    @property
    def supabase_provider(self) -> SupabaseStorageProvider:
        if not hasattr(self, "_supabase_provider") or self._supabase_provider is None:
            self._supabase_provider = SupabaseStorageProvider()
        return self._supabase_provider

    @supabase_provider.setter
    def supabase_provider(self, val):
        self._supabase_provider = val

    @property
    def r2_provider(self) -> R2StorageProvider:
        if not hasattr(self, "_r2_provider") or self._r2_provider is None:
            self._r2_provider = R2StorageProvider()
        return self._r2_provider

    @r2_provider.setter
    def r2_provider(self, val):
        self._r2_provider = val

    @property
    def local_provider(self) -> LocalStorageProvider:
        if not hasattr(self, "_local_provider") or self._local_provider is None:
            self._local_provider = LocalStorageProvider()
        return self._local_provider

    @local_provider.setter
    def local_provider(self, val):
        self._local_provider = val

    @property
    def active_provider(self) -> BaseStorageProvider:
        """Trả về Storage Provider đang hoạt động theo ưu tiên cấu hình."""
        target = settings.storage_provider.lower()
        if target == "supabase" and self.supabase_provider.is_active:
            return self.supabase_provider
        if target == "r2" and self.r2_provider.is_active:
            return self.r2_provider
        if self.supabase_provider.is_active:
            return self.supabase_provider
        if self.r2_provider.is_active:
            return self.r2_provider
        return self.local_provider

    @property
    def active_provider_name(self) -> str:
        return self.active_provider.provider_name

    @property
    def is_blob_active(self) -> bool:
        """True nếu có bất kỳ Cloud Blob Storage nào đang active (Supabase hoặc R2)."""
        return self.supabase_provider.is_active or self.r2_provider.is_active

    # --- Backward compatibility properties & aliases cho R2 ---
    @property
    def r2_enabled(self) -> bool:
        return self.r2_provider.r2_enabled

    @r2_enabled.setter
    def r2_enabled(self, val: bool):
        self.r2_provider.r2_enabled = val

    @property
    def r2_client(self):
        return self.r2_provider.r2_client

    @r2_client.setter
    def r2_client(self, val):
        self.r2_provider.r2_client = val
        self.r2_provider.r2_enabled = val is not None

    @property
    def is_r2_active(self) -> bool:
        """True only when Cloudflare R2 itself is configured and active."""
        return self.r2_provider.is_active

    @property
    def is_supabase_active(self) -> bool:
        return self.supabase_provider.is_active

    @property
    def is_firebase_active(self) -> bool:
        return bool(getattr(self, "firebase_enabled", False) and getattr(self, "firestore_db", None))

    def _get_lock(self, doc_key: str) -> threading.Lock:
        if not hasattr(self, "_locks_guard"):
            self._locks_guard = threading.RLock()
        if not hasattr(self, "_locks"):
            self._locks = {}
        with self._locks_guard:
            if doc_key not in self._locks:
                self._locks[doc_key] = threading.RLock()
            return self._locks[doc_key]

    def _cache_bible(self, job_id: str, doc_key: str, bible: BookBible) -> None:
        if not hasattr(self, "_bibles"):
            self._bibles = {}
        self._bibles[job_id] = bible
        self._bibles[doc_key] = bible

    # --- Unified Storage Operations ---

    def put_bytes(self, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> Optional[str]:
        url = self.active_provider.put_bytes(object_name, data, content_type=content_type)
        if self.active_provider != self.local_provider:
            self.local_provider.put_bytes(object_name, data, content_type=content_type)
        return url

    def get_bytes(self, object_name: str, raise_on_error: bool = False) -> Optional[bytes]:
        data = self.active_provider.get_bytes(object_name, raise_on_error=raise_on_error)
        if data is not None:
            return data
        if self.active_provider != self.local_provider:
            return self.local_provider.get_bytes(object_name, raise_on_error=raise_on_error)
        return None

    def upload_file(self, file_path: str, object_name: str, content_type: Optional[str] = None) -> Optional[str]:
        url = self.active_provider.upload_file(file_path, object_name, content_type=content_type)
        if self.active_provider != self.local_provider:
            self.local_provider.upload_file(file_path, object_name, content_type=content_type)
        return url

    def upload_file_to_r2(self, file_path: str, object_name: str) -> Optional[str]:
        """Tương thích ngược: tự động chuyển tiếp tới active provider (Supabase / R2)."""
        return self.upload_file(file_path, object_name)

    def file_exists(self, object_name: str) -> bool:
        if self.active_provider.file_exists(object_name):
            return True
        if self.active_provider != self.local_provider:
            return self.local_provider.file_exists(object_name)
        return False

    def file_exists_in_r2(self, object_name: str) -> bool:
        """Backward-compatible alias for checking the configured active storage."""
        return self.file_exists(object_name)

    def file_exists_on_r2(self, object_name: str) -> bool:
        """Check the R2 bucket specifically, never the active provider fallback."""
        return self.r2_provider.file_exists(object_name)

    def get_public_url(self, object_name: str) -> str:
        return self.active_provider.get_public_url(object_name.lstrip("/"))

    def upload_json(self, object_name: str, data: dict) -> bool:
        success = self.active_provider.put_json(object_name, data)
        if self.active_provider != self.local_provider:
            self.local_provider.put_json(object_name, data)
        return success

    def download_json(self, object_name: str, raise_on_error: bool = False) -> Optional[dict]:
        data = self.active_provider.get_json(object_name, raise_on_error=raise_on_error)
        if data is not None:
            return data
        if self.active_provider != self.local_provider:
            return self.local_provider.get_json(object_name, raise_on_error=raise_on_error)
        return None

    def _r2_put_json(self, object_name: str, data: dict) -> bool:
        """Tương thích ngược cho các lời gọi _r2_put_json cũ."""
        return self.upload_json(object_name, data)

    def _r2_get_json(self, object_name: str, raise_on_error: bool = False) -> Optional[dict]:
        """Tương thích ngược cho các lời gọi _r2_get_json cũ."""
        return self.download_json(object_name, raise_on_error=raise_on_error)

    def _r2_list_json_objects(self, prefix: str) -> List[dict]:
        """Tương thích ngược cho các lời gọi _r2_list_json_objects cũ."""
        return self.active_provider.list_json_objects(prefix)

    def delete_file(self, object_name: str) -> bool:
        cloud_success = self.active_provider.delete_file(object_name)
        local_success = self.local_provider.delete_file(object_name)
        return cloud_success or local_success

    def delete_files(self, object_names: List[str]) -> int:
        cloud_count = self.active_provider.delete_files(object_names)
        self.local_provider.delete_files(object_names)
        return cloud_count

    def list_files(self, prefix: str = "", raise_on_error: bool = False) -> List[str]:
        keys = set(self.active_provider.list_files(prefix, raise_on_error=raise_on_error))
        keys.update(self.local_provider.list_files(prefix))
        return sorted(list(keys))

    # --- Structured Data & Timelines ---

    def save_job(self, job: TranslationJob) -> None:
        if not hasattr(self, "_jobs"):
            self._jobs = {}
        self._jobs[job.job_id] = job

        if settings.structured_storage_backend in ("dual", "postgres"):
            try:
                with db_session() as session:
                    LibraryRepository.save_translation_job(session, job)
                    session.commit()
            except Exception as exc:
                if settings.structured_storage_backend == "postgres":
                    logger.error("Failed to save translation job to DB in postgres mode: %s", exc)
                    raise exc
                logger.warning("Failed to save translation job to DB in dual mode: %s", exc)

        if settings.structured_storage_backend in ("legacy", "dual"):
            if self.is_blob_active:
                self.upload_json(f"data/jobs/{job.job_id}.json", job.model_dump(mode="json"))
            if self.is_firebase_active:
                try:
                    ref = self.firestore_db.collection("translation_jobs").document(job.job_id)
                    ref.set(job.model_dump(mode="json"), merge=True)
                except Exception as exc:
                    logger.warning("Failed to sync job to Firestore, saved locally: %s", exc)

    def get_job(self, job_id: str) -> Optional[TranslationJob]:
        if not hasattr(self, "_jobs"):
            self._jobs = {}

        if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
            try:
                with db_session() as session:
                    db_job = LibraryRepository.get_translation_job(session, job_id)
                    if db_job:
                        self._jobs[job_id] = db_job
                        return db_job
            except Exception as exc:
                logger.warning("Failed to get job from DB: %s", exc)
            return self._jobs.get(job_id)

        if self.is_firebase_active:
            try:
                ref = self.firestore_db.collection("translation_jobs").document(job_id)
                snapshot = ref.get()
                if snapshot.exists:
                    job = TranslationJob.model_validate(snapshot.to_dict())
                    self._jobs[job_id] = job
                    return job
            except Exception as exc:
                logger.warning("Failed to fetch job from Firestore, using local memory: %s", exc)
        if self.is_blob_active and job_id not in self._jobs:
            data = self.download_json(f"data/jobs/{job_id}.json")
            if data:
                job = TranslationJob.model_validate(data)
                self._jobs[job_id] = job
                return job
        return self._jobs.get(job_id)

    @staticmethod
    def _as_delta(bible: BookBible) -> BookBibleDelta:
        return BookBibleDelta(
            new_characters=bible.characters,
            new_places=bible.places,
            new_terms=bible.terms,
            style_guide=bible.style_guide,
        )

    @staticmethod
    def _merge_full_bible(existing: Optional[BookBible], incoming: BookBible) -> BookBible:
        if existing is None:
            result = incoming.model_copy(deep=True)
            return BookBibleService.ensure_timeline(result)
        target = existing.model_copy(deep=True)
        if target.novel_id == "default" and incoming.novel_id != "default":
            target.novel_id = incoming.novel_id
        target = BookBibleService.merge_delta(target, StorageRepository._as_delta(incoming))
        observation_index = {
            item.observation_id: index
            for index, item in enumerate(target.address_observations)
        }
        for item in incoming.address_observations:
            if item.observation_id in observation_index:
                target.address_observations[observation_index[item.observation_id]] = item.model_copy(deep=True)
            else:
                target.address_observations.append(item.model_copy(deep=True))
                observation_index[item.observation_id] = len(target.address_observations) - 1
        change_index = {item.change_id: index for index, item in enumerate(target.pending_changes)}
        for item in incoming.pending_changes:
            if item.change_id in change_index:
                target.pending_changes[change_index[item.change_id]] = item.model_copy(deep=True)
            else:
                target.pending_changes.append(item.model_copy(deep=True))
                change_index[item.change_id] = len(target.pending_changes) - 1
        target.schema_version = max(target.schema_version, incoming.schema_version)
        target.bible_revision = max(target.bible_revision, incoming.bible_revision)
        return BookBibleService.ensure_timeline(target)

    def save_bible(self, job_id: str, bible: BookBible) -> None:
        if not hasattr(self, "_bibles"):
            self._bibles = {}
        started = time.perf_counter()
        doc_key = bible.novel_id if bible.novel_id and bible.novel_id != "default" else job_id
        lock = self._get_lock(doc_key)
        with lock:
            merge_started = time.perf_counter()
            existing = self._bibles.get(doc_key) or self._bibles.get(job_id)
            if existing is None:
                if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
                    try:
                        with db_session() as session:
                            existing = BookBibleRepository.get_book_bible(session, doc_key)
                    except Exception:
                        pass
                if existing is None and self.is_blob_active:
                    r2_data = self.download_json(f"data/bibles/{doc_key}.json")
                    if r2_data:
                        existing = BookBibleService.ensure_timeline(BookBible.model_validate(r2_data))
            merged = self._merge_full_bible(existing, bible)
            self._cache_bible(job_id, doc_key, merged)
            logger.info(
                "[TIMING] stage=book_bible_merge_storage.end novel=%s elapsed_ms=%.1f "
                "characters=%d observations=%d",
                doc_key,
                (time.perf_counter() - merge_started) * 1000,
                len(merged.characters),
                len(merged.address_observations),
            )

            # 1. Save to database if dual or postgres
            if settings.structured_storage_backend in ("dual", "postgres"):
                try:
                    with db_session() as session:
                        BookBibleRepository.save_book_bible(session, merged)
                        session.commit()
                except Exception as exc:
                    if settings.structured_storage_backend == "postgres":
                        logger.error("Failed to persist Book Bible to Database in postgres mode: %s", exc)
                        raise exc
                    logger.warning("Failed to persist Book Bible to Database in dual mode: %s", exc)

            # 2. Save to Blob Storage / Firestore if legacy or dual
            if settings.structured_storage_backend in ("legacy", "dual"):
                if self.is_blob_active:
                    self.upload_json(f"novels/{doc_key}/bible.json", merged.model_dump(mode="json"))

                if self.is_firebase_active:
                    try:
                        firestore_started = time.perf_counter()
                        ref = self.firestore_db.collection("book_bibles").document(doc_key)
                        ref.set(merged.model_dump(mode="json"), merge=True)
                        logger.info(
                            "[TIMING] stage=firestore_bible_write.end novel=%s elapsed_ms=%.1f",
                            doc_key,
                            (time.perf_counter() - firestore_started) * 1000,
                        )
                    except Exception as exc:
                        logger.warning("Failed to persist Book Bible to Firestore, saved locally: %s", exc)
            logger.info(
                "[TIMING] stage=book_bible_persist.total novel=%s elapsed_ms=%.1f",
                doc_key,
                (time.perf_counter() - started) * 1000,
            )

    def merge_bible_delta(
        self,
        job_or_novel_id: str,
        delta: BookBibleDelta,
        default_novel_id: Optional[str] = None,
    ) -> BookBible:
        if not hasattr(self, "_bibles"):
            self._bibles = {}
        lock = self._get_lock(job_or_novel_id)
        with lock:
            existing = self._bibles.get(job_or_novel_id)
            if existing is None:
                if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
                    try:
                        with db_session() as session:
                            existing = BookBibleRepository.get_book_bible(session, job_or_novel_id)
                    except Exception:
                        pass
                if existing is None and self.is_blob_active:
                    r2_data = self.download_json(f"novels/{job_or_novel_id}/bible.json") or self.download_json(f"data/bibles/{job_or_novel_id}.json")
                    if r2_data:
                        existing = BookBibleService.ensure_timeline(BookBible.model_validate(r2_data))
            target = existing.model_copy(deep=True) if existing else BookBible(
                novel_id=default_novel_id or job_or_novel_id
            )
            merged = BookBibleService.merge_delta(target, delta)
            self._cache_bible(job_or_novel_id, job_or_novel_id, merged)

            if settings.structured_storage_backend in ("dual", "postgres"):
                try:
                    with db_session() as session:
                        merged = BookBibleRepository.merge_delta_transactional(session, job_or_novel_id, delta)
                        session.commit()
                except Exception as exc:
                    if settings.structured_storage_backend == "postgres":
                        logger.error("Failed to merge Book Bible delta in DB (postgres mode): %s", exc)
                        raise exc
                    logger.warning("Failed to merge Book Bible delta in DB (dual mode): %s", exc)

            if settings.structured_storage_backend in ("legacy", "dual"):
                if self.is_blob_active:
                    self.upload_json(f"novels/{job_or_novel_id}/bible.json", merged.model_dump(mode="json"))

                if self.is_firebase_active:
                    try:
                        ref = self.firestore_db.collection("book_bibles").document(job_or_novel_id)
                        ref.set(merged.model_dump(mode="json"), merge=True)
                    except Exception as exc:
                        logger.warning("Failed to merge Bible delta to Firestore, saved locally: %s", exc)
            return merged

    def get_bible(self, job_id: str) -> Optional[BookBible]:
        if not hasattr(self, "_bibles"):
            self._bibles = {}

        if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
            try:
                with db_session() as session:
                    db_bible = BookBibleRepository.get_book_bible(session, job_id)
                    if db_bible:
                        self._bibles[job_id] = db_bible
                        return db_bible
            except Exception as exc:
                logger.warning("Failed to get Book Bible from Database: %s", exc)
            bible = self._bibles.get(job_id)
            return BookBibleService.ensure_timeline(bible) if bible else None

        if self.is_firebase_active:
            try:
                ref = self.firestore_db.collection("book_bibles").document(job_id)
                snapshot = ref.get()
                if snapshot.exists:
                    bible = BookBibleService.ensure_timeline(
                        BookBible.model_validate(snapshot.to_dict())
                    )
                    self._bibles[job_id] = bible
                    return bible
            except Exception as exc:
                logger.warning("Failed to fetch Book Bible from Firestore, using local memory: %s", exc)
        if self.is_blob_active and job_id not in self._bibles:
            data = self.download_json(f"novels/{job_id}/bible.json") or self.download_json(f"data/bibles/{job_id}.json")
            if data:
                bible = BookBibleService.ensure_timeline(BookBible.model_validate(data))
                self._bibles[job_id] = bible
                return bible

        bible = self._bibles.get(job_id)
        return BookBibleService.ensure_timeline(bible) if bible else None

    def review_pending_change(
        self,
        novel_id: str,
        change_id: str,
        status: str,
        reviewed_by: Optional[str] = None,
    ) -> Optional[BookBible]:
        lock = self._get_lock(novel_id)
        with lock:
            bible = self.get_bible(novel_id)
            if bible is None:
                return None
            change = next(
                (item for item in bible.pending_changes if item.change_id == change_id),
                None,
            )
            if change is None:
                return None
            if status not in {"approved", "rejected"}:
                raise ValueError("Invalid review status.")
            change.status = status
            change.reviewed_at = datetime.now(timezone.utc)
            change.reviewed_by = reviewed_by
            if status == "approved":
                for observation in bible.address_observations:
                    if observation.observation_id == (change.observation_id or change_id):
                        observation.resolution = "confirmed"
                if change.change_type == "canonical_correction":
                    for character in bible.characters:
                        if character.character_id == change.target_id:
                            character.vi_name = change.proposed_value
            bible.bible_revision += 1
            self.save_bible(novel_id, bible)
            return bible

    def list_jobs(self) -> List[TranslationJob]:
        if not hasattr(self, "_jobs"):
            self._jobs = {}

        if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
            try:
                with db_session() as session:
                    db_jobs = LibraryRepository.list_translation_jobs(session)
                    for j in db_jobs:
                        self._jobs[j.job_id] = j
                    return list(self._jobs.values())
            except Exception as exc:
                logger.warning("Failed to list translation jobs from DB: %s", exc)
            return list(self._jobs.values())

        if self.is_firebase_active:
            try:
                docs = self.firestore_db.collection("translation_jobs").stream()
                jobs = []
                for doc in docs:
                    job = TranslationJob.model_validate(doc.to_dict())
                    jobs.append(job)
                    self._jobs[job.job_id] = job
                return jobs
            except Exception as exc:
                logger.warning("Failed to list jobs from Firestore, falling back: %s", exc)
        if self.is_blob_active:
            items = self.active_provider.list_json_objects("data/jobs/")
            if items:
                jobs = []
                for data in items:
                    job = TranslationJob.model_validate(data)
                    jobs.append(job)
                    self._jobs[job.job_id] = job
                return jobs
        return list(self._jobs.values())

    def list_bibles(self) -> Dict[str, BookBible]:
        if not hasattr(self, "_bibles"):
            self._bibles = {}
        if self.is_firebase_active:
            try:
                docs = self.firestore_db.collection("book_bibles").stream()
                for doc in docs:
                    bible = BookBibleService.ensure_timeline(
                        BookBible.model_validate(doc.to_dict())
                    )
                    self._bibles[doc.id] = bible
                return self._bibles
            except Exception as exc:
                logger.warning("Failed to list Book Bibles from Firestore, falling back: %s", exc)
        if self.is_blob_active:
            items = self.active_provider.list_json_objects("novels/") + self.active_provider.list_json_objects("data/bibles/")
            if items:
                for data in items:
                    if isinstance(data, dict) and ("characters" in data or "character_events" in data):
                        try:
                            bible = BookBibleService.ensure_timeline(BookBible.model_validate(data))
                            doc_id = bible.novel_id or "default"
                            self._bibles[doc_id] = bible
                        except Exception:
                            pass
                return self._bibles
        return self._bibles

    def delete_bible(self, job_or_novel_id: str) -> bool:
        if not hasattr(self, "_bibles"):
            self._bibles = {}
        lock = self._get_lock(job_or_novel_id)
        with lock:
            self._bibles.pop(job_or_novel_id, None)

            if settings.structured_storage_backend in ("dual", "postgres"):
                try:
                    with db_session() as session:
                        BookBibleRepository.delete_book_bible(session, job_or_novel_id)
                        session.commit()
                except Exception as exc:
                    if settings.structured_storage_backend == "postgres":
                        logger.error("Failed to delete Book Bible from DB in postgres mode: %s", exc)
                        raise exc
                    logger.warning("Failed to delete Book Bible from DB in dual mode: %s", exc)

            if self.is_blob_active:
                for key_to_del in [f"novels/{job_or_novel_id}/bible.json", f"data/bibles/{job_or_novel_id}.json"]:
                    try:
                        self.delete_file(key_to_del)
                    except Exception as exc:
                        logger.warning("Failed to delete Book Bible %s from storage: %s", key_to_del, exc)
            if self.is_firebase_active:
                try:
                    self.firestore_db.collection("book_bibles").document(job_or_novel_id).delete()
                except Exception as exc:
                    logger.warning("Failed to delete Book Bible from Firestore: %s", exc)
            return True


storage_repo = StorageRepository()
