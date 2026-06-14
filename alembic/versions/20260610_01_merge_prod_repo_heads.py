"""merge prod and repo migration heads

Revision ID: 20260610_01
Revises: 20260609_02, 20260608_01
Create Date: 2026-06-13

Reconciliation merge of the two parallel migration lines that diverged at
20260504_01 (early May) and developed for ~1 month:

  repo line: 20260512_01 (run_schedules) -> 20260513_01 (worker_diagnostics)
             -> 20260514_01 (system_setting) -> 20260609_01 (evidence_ladder,
             renumbered from 20260607_01) -> 20260609_02 (knowledge_graph,
             renumbered from 20260607_02)

  prod line: 20260607_01 (ai_model_registry) -> 20260608_01
             (allow_cli_runs_without_credentials)

This is a no-op merge revision; it only joins the two heads into one so the
tree has a single head again. No schema changes.
"""

from __future__ import annotations


revision = "20260610_01"
down_revision = ("20260609_02", "20260608_01")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
