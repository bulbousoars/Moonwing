"""add first run setup flags

Revision ID: 20260427_03
Revises: 20260427_02
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa


revision = "20260427_03"
down_revision = "20260427_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    false_literal = sa.text("false") if is_pg else sa.text("0")
    op.add_column("users", sa.Column("is_bootstrap", sa.Boolean(), nullable=False, server_default=false_literal))
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=false_literal))
    if is_pg:
        op.alter_column("users", "is_bootstrap", server_default=None)
        op.alter_column("users", "must_change_password", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "is_bootstrap")
