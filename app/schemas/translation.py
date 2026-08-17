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
    created_at: str = ""
    completed_at: Optional[str] = None
