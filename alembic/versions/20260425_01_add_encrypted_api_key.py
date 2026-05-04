"""add encrypted_api_key to credentials

Revision ID: 20260425_01
Revises: 20260421_01
Create Date: 2026-04-25 10:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260425_01"
down_revision = "20260421_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("credentials", sa.Column("encrypted_api_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("credentials", "encrypted_api_key")
