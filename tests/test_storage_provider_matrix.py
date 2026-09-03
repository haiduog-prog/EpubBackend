from unittest.mock import MagicMock, patch
import pytest

from app.config import settings
from app.infrastructure.storage.facade import storage_repo


def test_storage_provider_local_never_calls_cloud_even_in_production(monkeypatch):
    """
    Xác minh khi storage_provider == 'local':
    Kể cả cấu hình nhầm APP_ENV=production và có cloudflare_r2_public_url,
    hệ thống tuyệt đối không thực hiện bất kỳ network call nào ra cloud.
    """
    monkeypatch.setattr(settings, "storage_provider", "local")
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "cloudflare_r2_public_url", "https://cdn.example.com")

    with patch("httpx.Client.get") as mock_get:
        # File không tồn tại trong local storage
        result = storage_repo.get_bytes("non-existent-local-object.json")
        assert result is None
        # Tuyệt đối không được gọi ra httpx / CDN
        mock_get.assert_not_called()


def test_storage_provider_cloud_allows_cdn_fallback_when_credentials_invalid(monkeypatch):
    """
    Xác minh khi cấu hình storage_provider là cloud ('supabase' hoặc 'r2') nhưng credentials lỗi/thiếu:
    Hệ thống vẫn được phép fallback đọc dữ liệu qua public CDN nếu có cấu hình cloudflare_r2_public_url.
    """
    monkeypatch.setattr(settings, "storage_provider", "supabase")
    monkeypatch.setattr(settings, "cloudflare_r2_public_url", "https://cdn.example.com")
    monkeypatch.setattr(storage_repo.r2_provider, "public_url", "https://cdn.example.com")

    # Force every credentialed provider inactive so this test never reaches a real
    # Supabase/R2 endpoint when the developer or CI machine has cloud env vars set.
    supabase = storage_repo.supabase_provider
    monkeypatch.setattr(supabase, "base_url", "")
    monkeypatch.setattr(supabase, "api_key", None)
    monkeypatch.setattr(supabase, "bucket", None)
    monkeypatch.setattr(storage_repo.r2_provider, "r2_enabled", False)
    monkeypatch.setattr(storage_repo.r2_provider, "r2_client", None)

    # Mock response từ public CDN
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"status": "ok", "from": "cdn"}'

    with patch("httpx.Client.get", return_value=mock_resp) as mock_get:
        result = storage_repo.get_bytes("novels/test-novel/meta.json")
        assert result == b'{"status": "ok", "from": "cdn"}'
        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        assert "cdn.example.com" in called_url
