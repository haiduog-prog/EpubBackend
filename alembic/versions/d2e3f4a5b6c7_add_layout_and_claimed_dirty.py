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
    with op.batch_alter_table("novels", schema=None) as batch_op:
        batch_op.add_column(sa.Column("layout_standardized", sa.Boolean(), nullable=False, server_default="false"))
        batch_op.alter_column("layout_standardized", server_default=None)

    with op.batch_alter_table("epub_build_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("claimed_dirty_chapters", JSONType, nullable=False, server_default=sa.text("'[]'")))
        batch_op.alter_column("claimed_dirty_chapters", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("epub_build_jobs", schema=None) as batch_op:
        batch_op.drop_column("claimed_dirty_chapters")

    with op.batch_alter_table("novels", schema=None) as batch_op:
        batch_op.drop_column("layout_standardized")
