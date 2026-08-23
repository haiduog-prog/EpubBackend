"""Compatibility entry point for Book Bible extraction pipelines."""

from app.modules.book_bible.application.facade import BookBibleService

__all__ = ["BookBibleService"]
