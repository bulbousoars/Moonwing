"""add execution_mode to runs

Revision ID: 20260425_02
Revises: 20260425_01
Create Date: 2026-04-25 11:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260425_02"
down_revision = "20260425_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("execution_mode", sa.String(16), nullable=False, server_default="api"))


def downgrade() -> None:
    op.drop_column("runs", "execution_mode")
