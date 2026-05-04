from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from moonwing.db.base import Base
from moonwing.db.models import AuditEvent, ServiceAccountToken, User
from moonwing.services.auth import hash_service_token
from moonwing.services.management import build_management_summary


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_management_summary_counts_users_roles_tokens_and_audit_events():
    session = _session()
    admin = User(email="admin", display_name="Admin", role="admin", status="active")
    viewer = User(email="viewer", display_name="Viewer", role="viewer", status="active")
    disabled = User(email="disabled", display_name="Disabled", role="viewer", status="disabled")
    service = User(
        email="scanner-bot",
        display_name="Scanner Bot",
        role="service_account",
        status="active",
        is_service_account=True,
    )
    session.add_all([admin, viewer, disabled, service])
    session.flush()
    session.add(ServiceAccountToken(user_id=service.id, display_name="worker", token_hash=hash_service_token("mwsvc_test")))
    session.add(AuditEvent(actor_user_id=admin.id, action="login", resource_type="user", resource_id="admin"))
    session.commit()

    summary = build_management_summary(session)

    assert summary == {
        "total_users": 4,
        "active_users": 3,
        "admins": 1,
        "service_accounts": 1,
        "active_tokens": 1,
        "audit_events": 1,
    }
