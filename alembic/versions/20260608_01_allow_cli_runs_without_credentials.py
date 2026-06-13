"""Allow CLI runs without credentials.

Revision ID: 20260608_01
Revises: 20260607_01
Create Date: 2026-06-08 00:00:00.000000
"""

from alembic import op


revision = "20260608_01"
down_revision = "20260607_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("runs", "credential_id", nullable=True)


def downgrade() -> None:
    op.alter_column("runs", "credential_id", nullable=False)
