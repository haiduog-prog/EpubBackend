from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class AddressTerm(BaseModel):
    with_person: str = Field(..., alias="with")
    self_term: str = Field(..., alias="self")
    other_term: str = Field(..., alias="other")
    context: str = ""

    model_config = {"populate_by_name": True}


class CharacterEntry(BaseModel):
    character_id: str = ""
    original_name: str
    vi_name: str
    role: str = ""
    voice_notes: str = ""
    address_terms: List[AddressTerm] = Field(default_factory=list)
    aliases: List[str] = Field(default_factory=list)


class AddressTermUpdate(BaseModel):
    character_original_name: str
    address_terms: List[AddressTerm] = Field(default_factory=list)


class AddressObservationCandidate(BaseModel):
    character_original_name: str
    counterpart_original_name: Optional[str] = None
    counterpart_text: str = ""
    self_term: str
    other_term: str
    context: str = ""
    evidence: str = ""
    confidence: float = 0.0
    change_type: Literal["same", "new", "replace", "uncertain"] = "new"
    explicit_transition: bool = False


class AddressObservation(BaseModel):
    observation_id: str
    character_id: str
    counterpart_id: Optional[str] = None
    counterpart_text: str = ""
    self_term: str
    other_term: str
    context: str = ""
    chapter_index: Optional[int] = None
    chapter_id: str = ""
    chunk_id: str = ""
    evidence: str = ""
    confidence: float = 0.0
    change_type: Literal["same", "new", "replace", "uncertain"] = "new"
    resolution: Literal["confirmed", "inferred", "pending", "rejected"] = "pending"
    explicit_transition: bool = False
    source: Literal["llm", "user", "legacy"] = "llm"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PendingBibleChange(BaseModel):
    change_id: str
    observation_id: Optional[str] = None
    change_type: Literal["canonical_correction", "identity_conflict", "address_conflict"]
    target_id: str
    old_value: str = ""
    proposed_value: str = ""
    evidence: str = ""
    confidence: float = 0.0
    chapter_index: Optional[int] = None
    status: Literal["pending", "approved", "rejected"] = "pending"
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None


class PlaceEntry(BaseModel):
    original_name: str
    vi_name: str
    notes: str = ""


class TermEntry(BaseModel):
    original_name: str
    vi_name: str
    category: str = ""
    notes: str = ""


class StyleGuide(BaseModel):
    genre: str = ""
    tone: str = ""
    era_setting: str = ""


class BookBibleDelta(BaseModel):
    new_characters: List[CharacterEntry] = Field(default_factory=list)
    new_address_terms_for_existing: List[AddressTermUpdate] = Field(default_factory=list)
    new_places: List[PlaceEntry] = Field(default_factory=list)
    new_terms: List[TermEntry] = Field(default_factory=list)
    address_observations: List[AddressObservationCandidate] = Field(default_factory=list)
    character_events: List[dict[str, Any]] = Field(default_factory=list)
    style_guide: Optional[StyleGuide] = None


class BookBible(BaseModel):
    novel_id: str = "default"
    schema_version: int = 2
    bible_revision: int = 0
    characters: List[CharacterEntry] = Field(default_factory=list)
    places: List[PlaceEntry] = Field(default_factory=list)
    terms: List[TermEntry] = Field(default_factory=list)
    style_guide: StyleGuide = Field(default_factory=StyleGuide)
    address_observations: List[AddressObservation] = Field(default_factory=list)
    pending_changes: List[PendingBibleChange] = Field(default_factory=list)



