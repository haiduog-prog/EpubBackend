from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.library import NovelStatus


class ReaderChapterSummary(BaseModel):
    chapter_index: int = Field(..., ge=1)
    chapter_id: str = ""
    chapter_title: str
    word_count: int = 0
    updated_at: Optional[str] = None
    content_url: Optional[str] = None
    content_urls: List[str] = Field(default_factory=list)


class ReaderBookSummary(BaseModel):
    novel_id: str
    title: str
    original_title: str = ""
    author: str = ""
    genre: List[str] = Field(default_factory=list)
    description: str = ""
    cover_url: Optional[str] = None
    status: NovelStatus = NovelStatus.ONGOING
    total_chapters: int = 0
    translated_chapters: int = 0


class ReaderBookDetail(ReaderBookSummary):
    chapters: List[ReaderChapterSummary] = Field(default_factory=list)


class ReaderChapterResponse(BaseModel):
    novel_id: str
    chapter: ReaderChapterSummary
    content: str
    previous_chapter: Optional[ReaderChapterSummary] = None
    next_chapter: Optional[ReaderChapterSummary] = None
