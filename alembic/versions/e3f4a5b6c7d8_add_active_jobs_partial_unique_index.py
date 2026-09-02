"""add active jobs partial unique index

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-09-02 21:08:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e3f4a5b6c7d8'
down_revision = 'd2e3f4a5b6c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop legacy index if exists
    op.execute(sa.text("DROP INDEX IF EXISTS uq_epub_build_jobs_active_novel;"))

    # Partial unique index ensuring at most ONE queued build job per novel at any time
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_epub_build_jobs_queued_novel
            ON epub_build_jobs (novel_id)
            WHERE status = 'queued';
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DROP INDEX IF EXISTS uq_epub_build_jobs_queued_novel;")
    )
