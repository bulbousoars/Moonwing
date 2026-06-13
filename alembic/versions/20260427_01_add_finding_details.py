"""add finding details

Revision ID: 20260427_01
Revises: 20260425_02
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260427_01"
down_revision = "20260425_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("findings", "details", server_default=None)


def downgrade() -> None:
    op.drop_column("findings", "details")
