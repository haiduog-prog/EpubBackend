from typing import List, Optional
from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field


class ChapterStatus(str, Enum):
    NOT_TRANSLATED = "not_translated"
    TRANSLATING = "translating"
    COMPLETED = "completed"
    FAILED = "failed"


class NovelStatus(str, Enum):
    ONGOING = "ongoing"
    COMPLETED = "completed"
    PAUSED = "paused"


class ChapterItem(BaseModel):
    chapter_index: int = Field(..., description="Số thứ tự chương (1-indexed)")
    chapter_id: str = Field(..., description="Mã định danh chương, vd: ch_0001")
    chapter_title: str = Field(..., description="Tiêu đề chương")
    status: ChapterStatus = Field(default=ChapterStatus.NOT_TRANSLATED)
    word_count: int = 0
    original_text_preview: str = ""
    translated_text_preview: str = ""
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    r2_original_key: str = ""
    r2_translated_key: str = ""
    r2_translated_url: Optional[str] = None


class NovelCreateRequest(BaseModel):
    title: str = Field(..., description="Tên truyện (tiếng Việt)")
    original_title: str = Field(default="", description="Tên gốc (tiếng Trung/Anh)")
    author: str = Field(default="", description="Tác giả")
    genre: List[str] = Field(default_factory=list, description="Danh sách thể loại")
    description: str = Field(default="", description="Tóm tắt nội dung")
    novel_id: Optional[str] = Field(default=None, description="Mã định danh slug (tự động tạo nếu để trống)")


class NovelUpdateRequest(BaseModel):
    title: Optional[str] = None
    original_title: Optional[str] = None
    author: Optional[str] = None
    genre: Optional[List[str]] = None
    description: Optional[str] = None
    status: Optional[NovelStatus] = None


class NovelSummary(BaseModel):
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
    created_at: str = ""
    updated_at: str = ""


class NovelMetadata(NovelSummary):
    chapters: List[ChapterItem] = Field(default_factory=list)


class ChapterCreateRequest(BaseModel):
    chapter_index: int
    chapter_title: str
    content: str


class ChapterTranslateRequest(BaseModel):
    api_key: Optional[str] = None
    provider: Optional[str] = "anthropic"
    model: Optional[str] = None


class ImportJobStatus(BaseModel):
    job_id: str
    novel_id: str = ""
    title: str = ""
    status: str = "pending"  # pending, processing, completed, failed
    current_step: str = ""
    current_chapter: int = 0
    total_chapters: int = 0
    added_chapters: int = 0
    skipped_chapters: int = 0
    progress_percentage: int = 0
    error_message: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None

