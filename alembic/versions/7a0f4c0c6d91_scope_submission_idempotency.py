"""scope submission idempotency keys by book and edition

Revision ID: 7a0f4c0c6d91
Revises: 2865c48fe099
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a0f4c0c6d91"
down_revision: Union[str, Sequence[str], None] = "2865c48fe099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE profile_submissions "
            "DROP CONSTRAINT IF EXISTS profile_submissions_idempotency_key_key"
        )
        op.create_unique_constraint(
            "uq_profile_submissions_scope_idempotency",
            "profile_submissions",
            ["book_id", "edition_id", "idempotency_key"],
        )
    elif bind.dialect.name == "sqlite":
        # SQLite's initial unnamed UNIQUE is represented by an auto-index. A
        # batch rebuild is required to remove it while preserving the data.
        with op.batch_alter_table(
            "profile_submissions",
            recreate="always",
            table_args=(
                sa.UniqueConstraint(
                    "book_id", "edition_id", "idempotency_key",
                    name="uq_profile_submissions_scope_idempotency",
                ),
            ),
        ):
            pass


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "uq_profile_submissions_scope_idempotency",
            "profile_submissions",
            type_="unique",
        )
        op.create_unique_constraint(
            "profile_submissions_idempotency_key_key",
            "profile_submissions",
            ["idempotency_key"],
        )
    elif bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "profile_submissions",
            recreate="always",
            table_args=(sa.UniqueConstraint("idempotency_key"),),
        ):
            pass
