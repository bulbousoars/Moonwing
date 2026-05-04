"""add email notifications and finding status

Revision ID: 20260428_01
Revises: 20260427_03
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260428_01"
down_revision = "20260427_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    true_literal = sa.text("true") if is_pg else sa.text("1")
    false_literal = sa.text("false") if is_pg else sa.text("0")
    notif_id_default = sa.text("gen_random_uuid()") if is_pg else None

    # ── SMTP config (single-row) ────────────────────────────────────
    op.create_table(
        "smtp_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("encrypted_password", sa.Text(), nullable=True),
        sa.Column("use_tls", sa.Boolean(), nullable=False, server_default=true_literal),
        sa.Column("from_address", sa.String(255), nullable=False),
        sa.Column("from_name", sa.String(255), nullable=False, server_default="Moonwing"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=false_literal),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── Notification preferences (one row per event type) ───────────
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=notif_id_default),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=false_literal),
        sa.Column("recipient_emails", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_type", name="uq_notification_pref_event_type"),
    )

    # Seed the 7 event types
    import uuid as _uuid
    notification_preferences = sa.table(
        "notification_preferences",
        sa.column("id", sa.Uuid()),
        sa.column("event_type", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("recipient_emails", sa.JSON),
    )
    seed_rows = []
    for event_type in (
        "run_started", "run_completed", "critical_finding", "finding_remediated",
        "user_added", "user_deleted", "user_permissions_changed",
    ):
        row = {"event_type": event_type, "enabled": False, "recipient_emails": []}
        if not is_pg:
            row["id"] = _uuid.uuid4()
        seed_rows.append(row)
    op.bulk_insert(notification_preferences, seed_rows)

    # ── Finding status + remediation tracking ───────────────────────
    op.add_column("findings", sa.Column("status", sa.String(32), nullable=False, server_default="open"))
    op.add_column("findings", sa.Column("remediated_at", sa.DateTime(timezone=True), nullable=True))
    if is_pg:
        op.add_column(
            "findings",
            sa.Column(
                "remediated_by",
                sa.Uuid(),
                sa.ForeignKey("users.id", name="fk_findings_remediated_by_users"),
                nullable=True,
            ),
        )
    else:
        # SQLite cannot add columns with FK constraints via plain ALTER;
        # use batch mode (copy-and-move strategy) and a named constraint.
        with op.batch_alter_table("findings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "remediated_by",
                    sa.Uuid(),
                    sa.ForeignKey("users.id", name="fk_findings_remediated_by_users"),
                    nullable=True,
                )
            )


def downgrade() -> None:
    op.drop_column("findings", "remediated_by")
    op.drop_column("findings", "remediated_at")
    op.drop_column("findings", "status")
    op.drop_table("notification_preferences")
    op.drop_table("smtp_config")
