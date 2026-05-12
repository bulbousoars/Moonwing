from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from moonwing.config import Settings
from moonwing.db.models import AuditEvent, ServiceAccountToken, User
from moonwing.services.auth import hash_password, verify_service_token


def audit(
    session: Session,
    *,
    action: str,
    resource_type: str,
    actor_user_id=None,
    resource_id: str | None = None,
    outcome: str = "success",
    metadata: dict | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            metadata_json=metadata or {},
        )
    )
    try:
        from moonwing.services.siem import emit_audit_event

        emit_audit_event(
            action=action,
            resource_type=resource_type,
            actor_user_id=actor_user_id,
            resource_id=resource_id,
            outcome=outcome,
            metadata=metadata or {},
        )
    except Exception:
        logger = logging.getLogger(__name__)
        logger.debug("siem audit emit skipped", exc_info=True)


def bootstrap_admin(session: Session, settings: Settings) -> User | None:
    email = settings.bootstrap_admin_email.strip().lower()
    user = session.query(User).filter(User.email == email).first()

    if user is None:
        previous_default = (
            session.query(User)
            .filter(
                User.email == "admin@moonwing.local",
                User.role == "admin",
                User.is_service_account.is_(False),
            )
            .first()
        )
        if previous_default is not None:
            user = previous_default

    if user is None:
        existing_admin = (
            session.query(User)
            .filter(
                User.role == "admin",
                User.is_service_account.is_(False),
                User.is_bootstrap.is_(False),
            )
            .first()
        )
        if existing_admin:
            return None

    if user is None:
        user = User(
            email=email,
            display_name=settings.bootstrap_admin_display_name,
        )
        session.add(user)

    user.email = email
    user.display_name = settings.bootstrap_admin_display_name
    user.password_hash = hash_password(settings.bootstrap_admin_password)
    user.role = "admin"
    user.status = "active"
    user.is_service_account = False
    user.is_bootstrap = True
    user.must_change_password = True
    audit(session, action="bootstrap_admin", resource_type="user", resource_id=email)
    session.commit()
    return user


def create_replacement_admin(
    session: Session,
    *,
    bootstrap_user: User,
    identifier: str,
    display_name: str,
    password: str,
) -> User:
    clean_identifier = identifier.strip().lower()
    if not clean_identifier:
        raise ValueError("identifier is required")
    if not display_name.strip():
        raise ValueError("display name is required")
    if not password:
        raise ValueError("password is required")
    if session.query(User).filter(User.email == clean_identifier, User.id != bootstrap_user.id).first():
        raise ValueError("identifier already exists")

    user = User(
        email=clean_identifier,
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role="admin",
        status="active",
        is_service_account=False,
        is_bootstrap=False,
        must_change_password=False,
    )
    session.add(user)
    session.flush()

    bootstrap_user.status = "disabled"
    bootstrap_user.must_change_password = False
    audit(
        session,
        action="first_run_admin_create",
        resource_type="user",
        actor_user_id=bootstrap_user.id,
        resource_id=clean_identifier,
    )
    session.commit()
    return user


def authenticate_service_token(session: Session, token: str) -> User | None:
    for stored in session.query(ServiceAccountToken).filter(ServiceAccountToken.status == "active").all():
        if verify_service_token(token, stored.token_hash):
            user = session.get(User, stored.user_id)
            if user and user.status == "active" and user.is_service_account:
                stored.last_used_at = datetime.now(timezone.utc)
                audit(
                    session,
                    action="service_token_auth",
                    resource_type="service_account_token",
                    actor_user_id=user.id,
                    resource_id=str(stored.id),
                )
                session.commit()
                return user
    return None
