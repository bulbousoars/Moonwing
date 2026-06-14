"""add evidence ladder to findings

Revision ID: 20260609_01
Revises: 20260514_01
Create Date: 2026-06-07

Renumbered from 20260607_01 during the prod<->repo reconciliation (2026-06-13)
to resolve a revision-id collision with prod's 20260607_01_add_ai_model_registry.
Logic unchanged.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260609_01"
down_revision = "20260514_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB(astext_type=sa.Text()) if is_pg else sa.JSON()
    json_list_default = sa.text("'[]'::jsonb") if is_pg else sa.text("'[]'")

    op.add_column(
        "findings",
        sa.Column(
            "evidence_level",
            sa.String(length=48),
            nullable=False,
            server_default="suspicion",
        ),
    )
    op.add_column(
        "findings",
        sa.Column(
            "evidence_history",
            json_type,
            nullable=False,
            server_default=json_list_default,
        ),
    )
    op.add_column(
        "findings",
        sa.Column("last_transition_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "findings",
        sa.Column("last_transition_by", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_findings_evidence_level",
        "findings",
        ["evidence_level"],
    )

    # Server defaults exist so the migration is online-safe; the ORM
    # supplies defaults for new rows, so drop the DB-side default on PG
    # (matches the project's existing migration style).
    if is_pg:
        op.alter_column("findings", "evidence_history", server_default=None)

    # FK for last_transition_by — created separately so the column add stays
    # SQLite-batch-friendly on older Alembic versions.
    with op.batch_alter_table("findings") as batch:
        batch.create_foreign_key(
            "fk_findings_last_transition_by_users",
            "users",
            ["last_transition_by"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("findings") as batch:
        batch.drop_constraint("fk_findings_last_transition_by_users", type_="foreignkey")
    op.drop_index("ix_findings_evidence_level", table_name="findings")
    op.drop_column("findings", "last_transition_by")
    op.drop_column("findings", "last_transition_at")
    op.drop_column("findings", "evidence_history")
    op.drop_column("findings", "evidence_level")
