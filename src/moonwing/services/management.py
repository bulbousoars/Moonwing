from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from moonwing.db.models import AuditEvent, ServiceAccountToken, User


def build_management_summary(session: Session) -> dict[str, int]:
    return {
        "total_users": session.query(func.count(User.id)).scalar() or 0,
        "active_users": session.query(func.count(User.id)).filter(User.status == "active").scalar() or 0,
        "admins": session.query(func.count(User.id)).filter(User.role == "admin", User.status == "active").scalar() or 0,
        "service_accounts": session.query(func.count(User.id)).filter(User.is_service_account.is_(True)).scalar() or 0,
        "active_tokens": session.query(func.count(ServiceAccountToken.id)).filter(ServiceAccountToken.status == "active").scalar() or 0,
        "audit_events": session.query(func.count(AuditEvent.id)).scalar() or 0,
    }
