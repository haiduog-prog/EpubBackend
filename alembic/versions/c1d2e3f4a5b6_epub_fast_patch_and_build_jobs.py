"""add epub fast patch and build jobs
 
Revision ID: c1d2e3f4a5b6
Revises: ab12cd34ef56
"""
 
from typing import Sequence, Union
 
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
 
 
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "ab12cd34ef56"
branch_labels = None
depends_on = None
 
JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
 
 
def upgrade() -> None:
    # Add columns to novels
    op.add_column("novels", sa.Column("current_epub_key", sa.String(), nullable=True))
    op.add_column("novels", sa.Column("desired_revision", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("novels", sa.Column("built_revision", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("novels", sa.Column("is_structural_dirty", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("novels", sa.Column("dirty_chapters", JSONType, nullable=False, server_default=sa.text("'[]'")))
    op.alter_column("novels", "desired_revision", server_default=None)
    op.alter_column("novels", "built_revision", server_default=None)
    op.alter_column("novels", "is_structural_dirty", server_default=None)
    op.alter_column("novels", "dirty_chapters", server_default=None)
 
    # Create epub_build_jobs table
    op.create_table(
        "epub_build_jobs",
        sa.Column("job_id", sa.String(), primary_key=True),
        sa.Column("novel_id", sa.String(), sa.ForeignKey("novels.novel_id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("strategy", sa.String(), nullable=False, server_default="fast_patch"),
        sa.Column("dirty_chapters", JSONType, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("is_structural", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("target_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("built_revision", sa.Integer(), nullable=True),
        sa.Column("epub_key", sa.String(), nullable=True),
        sa.Column("lease_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_epub_build_jobs_status_created", "epub_build_jobs", ["status", "created_at"])
    op.create_index("idx_epub_build_jobs_novel_status", "epub_build_jobs", ["novel_id", "status"])
 
 
def downgrade() -> None:
    op.drop_index("idx_epub_build_jobs_novel_status", table_name="epub_build_jobs")
    op.drop_index("idx_epub_build_jobs_status_created", table_name="epub_build_jobs")
    op.drop_table("epub_build_jobs")
 
    op.drop_column("novels", "dirty_chapters")
    op.drop_column("novels", "is_structural_dirty")
    op.drop_column("novels", "built_revision")
    op.drop_column("novels", "desired_revision")
    op.drop_column("novels", "current_epub_key")
