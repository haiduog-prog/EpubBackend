"""add semantic review metadata to chapters

Revision ID: ab12cd34ef56
Revises: 9f1c2d3e4b5a
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "ab12cd34ef56"
down_revision: Union[str, Sequence[str], None] = "9f1c2d3e4b5a"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("chapters", sa.Column("review_status", sa.String(), nullable=False, server_default="pending"))
    op.add_column("chapters", sa.Column("review_issues", JSONType, nullable=False, server_default=sa.text("'[]'")))
    op.add_column("chapters", sa.Column("reviewer_model", sa.String(), nullable=True))
    op.add_column("chapters", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chapters", sa.Column("review_error", sa.Text(), nullable=True))
    op.alter_column("chapters", "review_status", server_default=None)
    op.alter_column("chapters", "review_issues", server_default=None)


def downgrade() -> None:
    op.drop_column("chapters", "review_error")
    op.drop_column("chapters", "reviewed_at")
    op.drop_column("chapters", "reviewer_model")
    op.drop_column("chapters", "review_issues")
    op.drop_column("chapters", "review_status")
