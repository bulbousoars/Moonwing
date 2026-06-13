"""worker_diagnostics for CLI agent snapshots from moonwing-worker

Revision ID: 20260513_01
Revises: 20260512_01
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260513_01"
down_revision = "20260512_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_diagnostics",
        sa.Column("component", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("component"),
    )


def downgrade() -> None:
    op.drop_table("worker_diagnostics")
