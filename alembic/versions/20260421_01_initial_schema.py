"""initial schema

Revision ID: 20260421_01
Revises:
Create Date: 2026-04-21 13:45:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '20260421_01'
down_revision = None
branch_labels = None
depends_on = None


def _uuid_type(dialect_name: str):
    if dialect_name == 'postgresql':
        return postgresql.UUID(as_uuid=True)
    return sa.Uuid()


def _json_type(dialect_name: str):
    if dialect_name == 'postgresql':
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def _false_literal(dialect_name: str):
    return sa.text('false') if dialect_name == 'postgresql' else sa.text('0')


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    uuid_type = _uuid_type(dialect_name)
    json_type = _json_type(dialect_name)
    created_at_default = sa.text('CURRENT_TIMESTAMP')

    op.create_table(
        'users',
        sa.Column('id', uuid_type, primary_key=True),
        sa.Column('email', sa.String(length=255), nullable=False, unique=True),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=created_at_default, nullable=False),
    )
    op.create_table(
        'runtime_profiles',
        sa.Column('id', uuid_type, primary_key=True),
        sa.Column('name', sa.String(length=255), nullable=False, unique=True),
        sa.Column('allow_exploits', sa.Boolean(), nullable=False, server_default=_false_literal(dialect_name)),
        sa.Column('settings', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=created_at_default, nullable=False),
    )
    op.create_table(
        'targets',
        sa.Column('id', uuid_type, primary_key=True),
        sa.Column('target_type', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('source_metadata', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=created_at_default, nullable=False),
    )
    op.create_table(
        'credentials',
        sa.Column('id', uuid_type, primary_key=True),
        sa.Column('owner_user_id', uuid_type, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('scope', sa.String(length=32), nullable=False),
        sa.Column('provider', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('secret_ref', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=created_at_default, nullable=False),
    )
    op.create_index('ix_credentials_owner_user_id', 'credentials', ['owner_user_id'])
    op.create_table(
        'artifacts',
        sa.Column('id', uuid_type, primary_key=True),
        sa.Column('target_id', uuid_type, sa.ForeignKey('targets.id'), nullable=True),
        sa.Column('artifact_type', sa.String(length=64), nullable=False),
        sa.Column('object_key', sa.String(length=512), nullable=False),
        sa.Column('provenance', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=created_at_default, nullable=False),
    )
    op.create_index('ix_artifacts_target_id', 'artifacts', ['target_id'])
    op.create_table(
        'runs',
        sa.Column('id', uuid_type, primary_key=True),
        sa.Column('job_family', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('user_id', uuid_type, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('credential_id', uuid_type, sa.ForeignKey('credentials.id'), nullable=False),
        sa.Column('runtime_profile_id', uuid_type, sa.ForeignKey('runtime_profiles.id'), nullable=False),
        sa.Column('target_id', uuid_type, sa.ForeignKey('targets.id'), nullable=True),
        sa.Column('provider', sa.String(length=64), nullable=False),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('execution_snapshot', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=created_at_default, nullable=False),
    )
    op.create_index('ix_runs_user_id', 'runs', ['user_id'])
    op.create_index('ix_runs_credential_id', 'runs', ['credential_id'])
    op.create_index('ix_runs_runtime_profile_id', 'runs', ['runtime_profile_id'])
    op.create_index('ix_runs_target_id', 'runs', ['target_id'])
    op.create_table(
        'findings',
        sa.Column('id', uuid_type, primary_key=True),
        sa.Column('run_id', uuid_type, sa.ForeignKey('runs.id'), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('severity', sa.String(length=32), nullable=False),
        sa.Column('evidence_refs', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=created_at_default, nullable=False),
    )
    op.create_index('ix_findings_run_id', 'findings', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_findings_run_id', table_name='findings')
    op.drop_table('findings')
    op.drop_index('ix_runs_target_id', table_name='runs')
    op.drop_index('ix_runs_runtime_profile_id', table_name='runs')
    op.drop_index('ix_runs_credential_id', table_name='runs')
    op.drop_index('ix_runs_user_id', table_name='runs')
    op.drop_table('runs')
    op.drop_index('ix_artifacts_target_id', table_name='artifacts')
    op.drop_table('artifacts')
    op.drop_index('ix_credentials_owner_user_id', table_name='credentials')
    op.drop_table('credentials')
    op.drop_table('targets')
    op.drop_table('runtime_profiles')
    op.drop_table('users')
