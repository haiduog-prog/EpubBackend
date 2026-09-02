"""add epub build job progress columns

Revision ID: f4a5b6c7d8e9
Revises: d2e3f4a5b6c7
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("epub_build_jobs", sa.Column("current_step", sa.String(), nullable=True, server_default=""))
    op.alter_column("epub_build_jobs", "current_step", server_default=None)

    op.add_column("epub_build_jobs", sa.Column("current_chapter", sa.Integer(), nullable=True))

    op.add_column("epub_build_jobs", sa.Column("total_chapters", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("epub_build_jobs", "total_chapters", server_default=None)

    op.add_column("epub_build_jobs", sa.Column("processed_chapters", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("epub_build_jobs", "processed_chapters", server_default=None)

    op.add_column("epub_build_jobs", sa.Column("progress_percentage", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("epub_build_jobs", "progress_percentage", server_default=None)


def downgrade() -> None:
    op.drop_column("epub_build_jobs", "progress_percentage")
    op.drop_column("epub_build_jobs", "processed_chapters")
    op.drop_column("epub_build_jobs", "total_chapters")
    op.drop_column("epub_build_jobs", "current_chapter")
    op.drop_column("epub_build_jobs", "current_step")
