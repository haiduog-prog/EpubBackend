"""Backward-compatible library model imports."""

from app.modules.library.persistence.legacy_models import ChapterModel, NovelModel

__all__ = ["ChapterModel", "NovelModel"]
