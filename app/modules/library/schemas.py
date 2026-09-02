from typing import List, Optional
from enum import Enum
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from app.schemas.translation import TranslationPatch


class ChapterStatus(str, Enum):
    NOT_TRANSLATED = "not_translated"
    TRANSLATING = "translating"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class NovelStatus(str, Enum):
    ONGOING = "ongoing"
    COMPLETED = "completed"
    PAUSED = "paused"


class ChapterItem(BaseModel):
    chapter_index: int = Field(..., description="Số thứ tự chương (1-indexed)")
    chapter_id: str = Field(default="", description="Mã định danh chương, vd: ch_0001")
    chapter_title: str = Field(..., description="Tiêu đề chương")
    status: ChapterStatus = Field(default=ChapterStatus.NOT_TRANSLATED)
    word_count: int = 0
    original_text_preview: str = ""
    translated_text_preview: str = ""
    updated_at: Optional[str] = Field(default=None, description="Thời điểm dịch/cập nhật chương gần nhất (ISO 8601)")
    r2_original_key: str = ""
    r2_translated_key: str = ""
    r2_translated_url: Optional[str] = None
    review_status: str = "pending"
    review_issues: List[TranslationPatch] = Field(default_factory=list)
    reviewer_model: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_error: Optional[str] = None



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
    current_epub_key: Optional[str] = None
    desired_revision: int = 0
    built_revision: int = 0
    is_structural_dirty: bool = False
    layout_standardized: bool = False
    dirty_chapters: List[int] = Field(default_factory=list)


class NovelMetadata(NovelSummary):
    chapters: List[ChapterItem] = Field(default_factory=list)


class EpubBuildJobCreateRequest(BaseModel):
    target_chapters: Optional[str] = Field(default=None, description="Danh sách chương cần cập nhật, vd: 1,2,5-10")
    force_rebuild: bool = Field(default=False, description="Bắt buộc build toàn bộ thay vì fast patch")


class EpubBuildJobResponse(BaseModel):
    job_id: str
    novel_id: str
    status: str = "queued"  # queued, processing, completed, failed
    strategy: str = "fast_patch"  # fast_patch, full_rebuild
    dirty_chapters: List[int] = Field(default_factory=list)
    claimed_dirty_chapters: List[int] = Field(default_factory=list)
    is_structural: bool = False
    target_revision: int = 0
    built_revision: Optional[int] = None
    epub_key: Optional[str] = None
    download_url: Optional[str] = None
    attempts: int = 0
    error_message: Optional[str] = None
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None




class ChapterCreateRequest(BaseModel):
    chapter_index: int
    chapter_title: str
    content: str


class ChapterTranslateRequest(BaseModel):
    api_key: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    preview_only: bool = Field(default=False, description="Nếu True, chỉ trả về bản dịch để so sánh xem trước mà không ghi đè vào kho lưu trữ")


class ChapterTranslatePreviewResponse(BaseModel):
    novel_id: str
    chapter_index: int
    chapter_title: str
    original_text: str = ""
    previous_translated_text: str = ""
    new_translated_text: str = ""
    word_count: int = 0
    model: Optional[str] = None
    provider: Optional[str] = None


class ChapterApplyTranslationRequest(BaseModel):
    content: str = Field(..., description="Nội dung bản dịch mới cần áp dụng")



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
    updated_chapters: int = 0
    progress_percentage: int = 0
    error_message: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None


class ChapterStatusDetail(BaseModel):
    index: int
    title: str
    has_original: bool
    has_translated: bool
    status: ChapterStatus


class MissingChaptersResponse(BaseModel):
    novel_id: str
    title: str
    total_chapters_recorded: int
    max_chapter_index: int
    existing_original_count: int
    existing_translated_count: int
    missing_original_indices: List[int]
    missing_translated_indices: List[int]
    chapters_detail: Optional[List[ChapterStatusDetail]] = None


class BulkDeleteNovelsRequest(BaseModel):
    novel_ids: List[str] = Field(..., min_length=1, description="Danh sách novel_id cần xóa")


class BulkDeleteNovelsResponse(BaseModel):
    deleted_count: int = Field(..., description="Số lượng truyện đã xóa thành công")
    failed_ids: List[str] = Field(default_factory=list, description="Danh sách ID truyện xóa thất bại hoặc không tìm thấy")
    message: str = Field(default="", description="Thông báo kết quả")


