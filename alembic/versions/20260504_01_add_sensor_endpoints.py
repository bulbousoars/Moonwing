"""add moonwing sensor endpoints

Revision ID: 20260504_01
Revises: 20260428_02
Create Date: 2026-05-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260504_01"
down_revision = "20260428_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sensor_endpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("os_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("sensor_version", sa.String(length=64), server_default="", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("inventory", sa.JSON(), nullable=False),
        sa.Column("network", sa.JSON(), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sensor_endpoints_hostname", "sensor_endpoints", ["hostname"])
    op.create_index("ix_sensor_endpoints_platform", "sensor_endpoints", ["platform"])
    op.create_table(
        "sensor_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sensor_id", sa.Uuid(), nullable=False),
        sa.Column("task_type", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensor_endpoints.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sensor_tasks_sensor_id", "sensor_tasks", ["sensor_id"])
    op.create_index("ix_sensor_tasks_status", "sensor_tasks", ["status"])
    op.create_table(
        "sensor_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sensor_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=32), server_default="info", nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensor_endpoints.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sensor_events_sensor_id", "sensor_events", ["sensor_id"])
    op.create_index("ix_sensor_events_event_type", "sensor_events", ["event_type"])
    op.create_index("ix_sensor_events_severity", "sensor_events", ["severity"])


def downgrade() -> None:
    op.drop_index("ix_sensor_events_severity", table_name="sensor_events")
    op.drop_index("ix_sensor_events_event_type", table_name="sensor_events")
    op.drop_index("ix_sensor_events_sensor_id", table_name="sensor_events")
    op.drop_table("sensor_events")
    op.drop_index("ix_sensor_tasks_status", table_name="sensor_tasks")
    op.drop_index("ix_sensor_tasks_sensor_id", table_name="sensor_tasks")
    op.drop_table("sensor_tasks")
    op.drop_index("ix_sensor_endpoints_platform", table_name="sensor_endpoints")
    op.drop_index("ix_sensor_endpoints_hostname", table_name="sensor_endpoints")
    op.drop_table("sensor_endpoints")
