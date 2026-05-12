from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from moonwing.db.base import Base
from moonwing.db.models import artifact, audit_event, credential, finding, privileged_access_grant, run, run_schedule, runtime_profile, sensor, service_account_token, target, user  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Compose and production installs set this; overrides alembic.ini default for localhost dev.
_override = os.environ.get("MOONWING_DATABASE_URL")
if _override:
    config.set_main_option("sqlalchemy.url", _override)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option('sqlalchemy.url')
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
