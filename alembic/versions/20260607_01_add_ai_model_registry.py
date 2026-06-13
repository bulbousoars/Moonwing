"""add ai model registry

Revision ID: 20260607_01
Revises: 20260504_01
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_01"
down_revision = "20260504_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_models',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('provider', sa.String(length=64), nullable=False),
        sa.Column('execution_mode', sa.String(length=16), nullable=False),
        sa.Column('model_id', sa.String(length=255), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('last_discovered_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'execution_mode', 'model_id', name='uq_ai_models_provider_mode_model'),
    )
    op.create_index('ix_ai_models_provider', 'ai_models', ['provider'])
    op.create_index('ix_ai_models_execution_mode', 'ai_models', ['execution_mode'])


def downgrade() -> None:
    op.drop_index('ix_ai_models_execution_mode', table_name='ai_models')
    op.drop_index('ix_ai_models_provider', table_name='ai_models')
    op.drop_table('ai_models')
