"""Backward-compatible imports for infrastructure storage.

The implementation now lives under ``app.infrastructure.storage``. This module
remains stable for existing API, CLI and test imports.
"""

from app.infrastructure.storage.legacy_storage import (
    BaseStorageProvider,
    LocalStorageProvider,
    R2StorageProvider,
    StorageRepository,
    SupabaseStorageProvider,
    storage_repo,
)

__all__ = [
    "BaseStorageProvider",
    "LocalStorageProvider",
    "R2StorageProvider",
    "StorageRepository",
    "SupabaseStorageProvider",
    "storage_repo",
]
