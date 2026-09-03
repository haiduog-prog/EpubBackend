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
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("novels", schema=None) as batch_op:
            batch_op.add_column(sa.Column("current_epub_key", sa.String(), nullable=True))
            batch_op.add_column(sa.Column("desired_revision", sa.Integer(), nullable=False, server_default="0"))
            batch_op.add_column(sa.Column("built_revision", sa.Integer(), nullable=False, server_default="0"))
            batch_op.add_column(sa.Column("is_structural_dirty", sa.Boolean(), nullable=False, server_default="false"))
            batch_op.add_column(sa.Column("dirty_chapters", JSONType, nullable=False, server_default=sa.text("'[]'")))
    else:
        with op.batch_alter_table("novels", schema=None) as batch_op:
            batch_op.add_column(sa.Column("current_epub_key", sa.String(), nullable=True))
            batch_op.add_column(sa.Column("desired_revision", sa.Integer(), nullable=False, server_default="0"))
            batch_op.add_column(sa.Column("built_revision", sa.Integer(), nullable=False, server_default="0"))
            batch_op.add_column(sa.Column("is_structural_dirty", sa.Boolean(), nullable=False, server_default="false"))
            batch_op.add_column(sa.Column("dirty_chapters", JSONType, nullable=False, server_default=sa.text("'[]'")))
            batch_op.alter_column("desired_revision", server_default=None)
            batch_op.alter_column("built_revision", server_default=None)
            batch_op.alter_column("is_structural_dirty", server_default=None)
            batch_op.alter_column("dirty_chapters", server_default=None)
 
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
 
    with op.batch_alter_table("novels", schema=None) as batch_op:
        batch_op.drop_column("dirty_chapters")
        batch_op.drop_column("is_structural_dirty")
        batch_op.drop_column("built_revision")
        batch_op.drop_column("desired_revision")
        batch_op.drop_column("current_epub_key")
