"""system_setting table for UI-managed flags (e.g. web terminal)

Revision ID: 20260514_01
Revises: 20260513_01
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260514_01"
down_revision = "20260513_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_setting",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("system_setting")
