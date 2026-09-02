"""add layout standardized and claimed dirty chapters

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("novels", sa.Column("layout_standardized", sa.Boolean(), nullable=False, server_default="false"))
    op.alter_column("novels", "layout_standardized", server_default=None)

    op.add_column("epub_build_jobs", sa.Column("claimed_dirty_chapters", JSONType, nullable=False, server_default=sa.text("'[]'")))
    op.alter_column("epub_build_jobs", "claimed_dirty_chapters", server_default=None)


def downgrade() -> None:
    op.drop_column("epub_build_jobs", "claimed_dirty_chapters")
    op.drop_column("novels", "layout_standardized")
