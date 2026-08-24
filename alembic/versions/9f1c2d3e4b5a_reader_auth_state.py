"""add per-user reader settings and progress

Revision ID: 9f1c2d3e4b5a
Revises: 1a2b3c4d5e6f
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "9f1c2d3e4b5a"
down_revision: Union[str, Sequence[str], None] = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "reader_user_settings",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("preferences", JSONType, nullable=False),
        sa.Column("local_migrated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "reader_progress",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("novel_id", sa.String(), nullable=False),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column("scroll_top", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.novel_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "novel_id"),
    )
    op.create_index("idx_reader_progress_user_updated", "reader_progress", ["user_id", "updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_reader_progress_user_updated", table_name="reader_progress")
    op.drop_table("reader_progress")
    op.drop_table("reader_user_settings")