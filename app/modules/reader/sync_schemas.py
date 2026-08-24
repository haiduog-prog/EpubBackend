from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ReaderProgressPayload(BaseModel):
    novel_id: str = Field(..., min_length=1, max_length=128)
    chapter_index: int = Field(..., ge=1)
    scroll_top: int = Field(default=0, ge=0)


class ReaderProgressUpdatePayload(BaseModel):
    chapter_index: int = Field(..., ge=1)
    scroll_top: int = Field(default=0, ge=0)

class ReaderPreferencesPayload(BaseModel):
    preferences: Dict[str, Any] = Field(default_factory=dict)


class ReaderLocalMigrationPayload(BaseModel):
    preferences: Dict[str, Any] = Field(default_factory=dict)
    progress: List[ReaderProgressPayload] = Field(default_factory=list)


class ReaderProgressState(ReaderProgressPayload):
    updated_at: datetime


class ReaderStateResponse(BaseModel):
    user_id: str
    preferences: Dict[str, Any] = Field(default_factory=dict)
    local_migrated_at: Optional[datetime] = None
    progress: List[ReaderProgressState] = Field(default_factory=list)