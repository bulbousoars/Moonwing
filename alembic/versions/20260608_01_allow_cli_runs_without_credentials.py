"""Allow CLI runs without credentials.

Revision ID: 20260608_01
Revises: 20260607_01
Create Date: 2026-06-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260608_01"
down_revision = "20260607_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch_alter_table so this also runs on SQLite (used by the test suite);
    # on PostgreSQL it emits a plain ALTER COLUMN.
    with op.batch_alter_table("runs") as batch_op:
        batch_op.alter_column("credential_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.alter_column("credential_id", existing_type=sa.Uuid(), nullable=False)
