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
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("chapters", schema=None) as batch_op:
            batch_op.add_column(sa.Column("review_status", sa.String(), nullable=False, server_default="pending"))
            batch_op.add_column(sa.Column("review_issues", JSONType, nullable=False, server_default=sa.text("'[]'")))
            batch_op.add_column(sa.Column("reviewer_model", sa.String(), nullable=True))
            batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("review_error", sa.Text(), nullable=True))
    else:
        with op.batch_alter_table("chapters", schema=None) as batch_op:
            batch_op.add_column(sa.Column("review_status", sa.String(), nullable=False, server_default="pending"))
            batch_op.add_column(sa.Column("review_issues", JSONType, nullable=False, server_default=sa.text("'[]'")))
            batch_op.add_column(sa.Column("reviewer_model", sa.String(), nullable=True))
            batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("review_error", sa.Text(), nullable=True))
            batch_op.alter_column("review_status", server_default=None)
            batch_op.alter_column("review_issues", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("chapters", schema=None) as batch_op:
        batch_op.drop_column("review_error")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewer_model")
        batch_op.drop_column("review_issues")
        batch_op.drop_column("review_status")
