import sys
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from moonwing.db.models import Credential, Run, RuntimeProfileRecord, Target, User


@pytest.fixture
def session_factory(tmp_path):
    database_path = tmp_path / 'moonwing-test.db'
    database_url = f'sqlite:///{database_path}'

    alembic_config = Config(str(ROOT / 'alembic.ini'))
    alembic_config.set_main_option('script_location', str(ROOT / 'alembic'))
    alembic_config.set_main_option('prepend_sys_path', str(SRC))
    alembic_config.set_main_option('sqlalchemy.url', database_url)
    command.upgrade(alembic_config, 'head')

    engine = create_engine(database_url, future=True)

    @event.listens_for(engine, 'connect')
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def test_initial_schema_persists_user_credential_and_run(session_factory):
    session = session_factory()

    assert session.execute(text('select 1')).scalar() == 1
    assert hasattr(session.bind, 'dialect')

    user = User(email='user@example.com', display_name='User One')
    session.add(user)
    session.flush()

    credential = Credential(
        owner_user_id=user.id,
        scope='user',
        provider='openai',
        display_name='Primary OpenAI',
        secret_ref='secret://credential/openai-primary',
    )
    runtime = RuntimeProfileRecord(name='default-network', settings={'depth': 'standard'})
    target = Target(target_type='host', display_name='192.168.1.215', source_metadata={'source': 'manual'})
    session.add_all([credential, runtime, target])
    session.flush()

    run = Run(
        job_family='network_scan',
        status='queued',
        user_id=user.id,
        credential_id=credential.id,
        runtime_profile_id=runtime.id,
        target_id=target.id,
        provider='openai',
        model='gpt-5.2',
        execution_snapshot={'provider': 'openai', 'model': 'gpt-5.2'},
    )
    session.add(run)
    session.commit()

    persisted = session.get(Run, run.id)
    assert persisted is not None
    assert persisted.provider == 'openai'
    assert persisted.execution_snapshot['model'] == 'gpt-5.2'


def test_initial_schema_enforces_foreign_keys(session_factory):
    session = session_factory()

    bad_run = Run(
        job_family='network_scan',
        status='queued',
        user_id=UUID('00000000-0000-0000-0000-000000000001'),
        credential_id=UUID('00000000-0000-0000-0000-000000000002'),
        runtime_profile_id=UUID('00000000-0000-0000-0000-000000000003'),
        target_id=UUID('00000000-0000-0000-0000-000000000004'),
        provider='openai',
        model='gpt-5.2',
        execution_snapshot={'provider': 'openai'},
    )
    session.add(bad_run)

    with pytest.raises(IntegrityError):
        session.commit()
