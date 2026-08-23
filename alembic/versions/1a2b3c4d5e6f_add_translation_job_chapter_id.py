"""persist translation job chapter ids

Revision ID: 1a2b3c4d5e6f
Revises: 7a0f4c0c6d91
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "7a0f4c0c6d91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "translation_jobs",
        sa.Column("chapter_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("translation_jobs", "chapter_id")
