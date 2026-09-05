"""add optimistic chapter revision

Revision ID: g5b6c7d8e9f0
Revises: f4a5b6c7d8e9
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("chapters", schema=None) as batch_op:
            batch_op.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
    else:
        with op.batch_alter_table("chapters", schema=None) as batch_op:
            batch_op.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
            batch_op.alter_column("revision", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("chapters", schema=None) as batch_op:
        batch_op.drop_column("revision")
