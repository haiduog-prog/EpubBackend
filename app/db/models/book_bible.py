from sqlalchemy import String, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

JSONType = JSON().with_variant(JSONB, 'postgresql')


class BookBibleModel(Base, TimestampMixin):
    __tablename__ = 'book_bibles'

    novel_id: Mapped[str] = mapped_column(String, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    bible_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
