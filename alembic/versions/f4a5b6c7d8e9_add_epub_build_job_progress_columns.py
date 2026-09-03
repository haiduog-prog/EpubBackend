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
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("epub_build_jobs", schema=None) as batch_op:
            batch_op.add_column(sa.Column("current_step", sa.String(), nullable=True, server_default=""))
            batch_op.add_column(sa.Column("current_chapter", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("total_chapters", sa.Integer(), nullable=False, server_default="0"))
            batch_op.add_column(sa.Column("processed_chapters", sa.Integer(), nullable=False, server_default="0"))
            batch_op.add_column(sa.Column("progress_percentage", sa.Integer(), nullable=False, server_default="0"))
    else:
        with op.batch_alter_table("epub_build_jobs", schema=None) as batch_op:
            batch_op.add_column(sa.Column("current_step", sa.String(), nullable=True, server_default=""))
            batch_op.alter_column("current_step", server_default=None)

            batch_op.add_column(sa.Column("current_chapter", sa.Integer(), nullable=True))

            batch_op.add_column(sa.Column("total_chapters", sa.Integer(), nullable=False, server_default="0"))
            batch_op.alter_column("total_chapters", server_default=None)

            batch_op.add_column(sa.Column("processed_chapters", sa.Integer(), nullable=False, server_default="0"))
            batch_op.alter_column("processed_chapters", server_default=None)

            batch_op.add_column(sa.Column("progress_percentage", sa.Integer(), nullable=False, server_default="0"))
            batch_op.alter_column("progress_percentage", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("epub_build_jobs", schema=None) as batch_op:
        batch_op.drop_column("progress_percentage")
        batch_op.drop_column("processed_chapters")
        batch_op.drop_column("total_chapters")
        batch_op.drop_column("current_chapter")
        batch_op.drop_column("current_step")
