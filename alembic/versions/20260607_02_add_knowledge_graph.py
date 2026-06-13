"""knowledge graph nodes and edges

Revision ID: 20260607_02
Revises: 20260607_01
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260607_02"
down_revision = "20260607_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB(astext_type=sa.Text()) if is_pg else sa.JSON()
    json_default = sa.text("'{}'::jsonb") if is_pg else sa.text("'{}'")

    op.create_table(
        "kg_nodes",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("stable_key", sa.String(length=512), nullable=False),
        sa.Column("attrs", json_type, nullable=False, server_default=json_default),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["source_run_id"], ["runs.id"], name="fk_kg_nodes_source_run"),
        sa.UniqueConstraint("kind", "stable_key", name="uq_kg_nodes_kind_key"),
    )
    op.create_index("ix_kg_nodes_kind", "kg_nodes", ["kind"])
    op.create_index("ix_kg_nodes_source_run_id", "kg_nodes", ["source_run_id"])

    op.create_table(
        "kg_edges",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("src_id", sa.Uuid(), nullable=False),
        sa.Column("dst_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("attrs", json_type, nullable=False, server_default=json_default),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["src_id"], ["kg_nodes.id"], name="fk_kg_edges_src", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["dst_id"], ["kg_nodes.id"], name="fk_kg_edges_dst", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"], ["runs.id"], name="fk_kg_edges_source_run"
        ),
        sa.UniqueConstraint("src_id", "dst_id", "kind", name="uq_kg_edges_src_dst_kind"),
    )
    op.create_index("ix_kg_edges_kind", "kg_edges", ["kind"])
    op.create_index("ix_kg_edges_src", "kg_edges", ["src_id"])
    op.create_index("ix_kg_edges_dst", "kg_edges", ["dst_id"])
    op.create_index("ix_kg_edges_source_run_id", "kg_edges", ["source_run_id"])


def downgrade() -> None:
    op.drop_index("ix_kg_edges_source_run_id", table_name="kg_edges")
    op.drop_index("ix_kg_edges_dst", table_name="kg_edges")
    op.drop_index("ix_kg_edges_src", table_name="kg_edges")
    op.drop_index("ix_kg_edges_kind", table_name="kg_edges")
    op.drop_table("kg_edges")
    op.drop_index("ix_kg_nodes_source_run_id", table_name="kg_nodes")
    op.drop_index("ix_kg_nodes_kind", table_name="kg_nodes")
    op.drop_table("kg_nodes")
