"""Backward-compatible library model imports."""

from app.modules.library.persistence.legacy_models import ChapterModel, NovelModel, EpubBuildJobModel

__all__ = ["ChapterModel", "NovelModel", "EpubBuildJobModel"]
