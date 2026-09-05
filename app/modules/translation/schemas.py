from typing import List, Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field


class InputType(str, Enum):
    TXT = "txt"
    EPUB = "epub"
    HTML = "html"


class HTMLInputItem(BaseModel):
    id: str = Field(..., description="Mã định danh cho khối text HTML")
    text: str = Field(..., description="Văn bản gốc cần dịch")
    # Marker-protected representation used by the HTML translator.  ``text``
    # remains the visible-text compatibility field for existing callers.
    protected_text: Optional[str] = Field(default=None, description="Nội dung có marker bảo vệ inline HTML")


class HTMLTranslationItem(BaseModel):
    id: str = Field(..., description="Mã định danh tương ứng với HTMLInputItem")
    text_vi: str = Field(..., description="Bản dịch tiếng Việt thuần")


class HTMLTranslationOutput(BaseModel):
    """Schema cho Structured Outputs của Prompt 3"""
    translations: List[HTMLTranslationItem] = Field(default_factory=list)


class QAIssue(BaseModel):
    issue: str = Field(..., description="Mô tả điểm không nhất quán")
    found: str = Field(..., description="Từ/cụm từ thực tế phát hiện")
    expected: str = Field(..., description="Từ/cụm từ đúng theo Book Bible")
    location: str = Field(..., description="Trích đoạn ngắn chứa lỗi")


class QAReport(BaseModel):
    """Schema cho Structured Outputs của Prompt 4"""
    issues: List[QAIssue] = Field(default_factory=list)


class TranslationPatch(BaseModel):
    """A minimal exact replacement proposed by the semantic reviewer."""

    old_text: str = Field(..., min_length=1)
    replacement: str = Field(..., min_length=1)
    reason: str = ""
    confidence: float = Field(..., ge=0.0, le=1.0)


class SemanticReviewReport(BaseModel):
    """Structured output returned by the chapter-level semantic reviewer."""

    issues: List[TranslationPatch] = Field(default_factory=list)


class TranslationPatch(BaseModel):
    """A minimal exact replacement proposed by the semantic reviewer."""

    old_text: str = Field(..., min_length=1)
    replacement: str = Field(..., min_length=1)
    reason: str = ""
    confidence: float = Field(..., ge=0.0, le=1.0)


class SemanticReviewReport(BaseModel):
    """Structured output returned by the chapter-level semantic reviewer."""

    issues: List[TranslationPatch] = Field(default_factory=list)


class JobStatusEnum(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TranslationJob(BaseModel):
    job_id: str
    filename: str
    input_type: InputType
    status: JobStatusEnum = JobStatusEnum.PENDING
    progress_percentage: float = 0.0
    current_step: str = ""
    error_message: Optional[str] = None
    translated_file_path: Optional[str] = None
    r2_url: Optional[str] = None
    provider: str = "gemini"
    model: Optional[str] = None
    novel_id: str = ""
    chapter_index: int = 0
    chapter_id: Optional[str] = None
    created_at: str = ""
    completed_at: Optional[str] = None
