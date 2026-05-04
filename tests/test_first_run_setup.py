from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from moonwing.db.base import Base
from moonwing.db.models import AuditEvent, ServiceAccountToken, User
from moonwing.services.auth import verify_password
from moonwing.services.iam import bootstrap_admin, create_replacement_admin


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _settings():
    return SimpleNamespace(
        bootstrap_admin_email="admin",
        bootstrap_admin_password="admin",
        bootstrap_admin_display_name="Bootstrap Admin",
    )


def test_bootstrap_admin_uses_local_admin_identifier_and_requires_setup():
    session = _session()

    user = bootstrap_admin(session, _settings())

    assert user.email == "admin"
    assert user.display_name == "Bootstrap Admin"
    assert user.role == "admin"
    assert user.is_bootstrap is True
    assert user.must_change_password is True
    assert verify_password("admin", user.password_hash)


def test_bootstrap_admin_converts_previous_default_admin_to_bootstrap_account():
    session = _session()
    session.add(
        User(
            email="admin@moonwing.local",
            display_name="Moonwing Admin",
            password_hash="old",
            role="admin",
            status="active",
            is_service_account=False,
        )
    )
    session.commit()

    user = bootstrap_admin(session, _settings())

    assert user.email == "admin"
    assert user.display_name == "Bootstrap Admin"
    assert user.is_bootstrap is True
    assert verify_password("admin", user.password_hash)
    assert session.query(User).count() == 1


def test_create_replacement_admin_disables_bootstrap_user():
    session = _session()
    bootstrap_user = bootstrap_admin(session, _settings())

    new_admin = create_replacement_admin(
        session,
        bootstrap_user=bootstrap_user,
        identifier="owner@example.com",
        display_name="Owner Admin",
        password="better-password",
    )

    assert new_admin.email == "owner@example.com"
    assert new_admin.role == "admin"
    assert new_admin.is_bootstrap is False
    assert new_admin.must_change_password is False
    assert verify_password("better-password", new_admin.password_hash)
    assert bootstrap_user.status == "disabled"
