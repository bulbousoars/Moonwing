from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from moonwing.db.models import AuditEvent, LdapConfig, User
from moonwing.services.crypto import CryptoError, decrypt_api_key
from moonwing.services.iam import audit


ROLE_PRIORITY = ["admin", "security_engineer", "operator", "analyst", "viewer"]
VALID_ROLES = set(ROLE_PRIORITY)


LDAP_PROVIDER_PRESETS = {
    "generic": {
        "label": "Generic LDAP",
        "port": 636,
        "use_ssl": True,
        "start_tls": False,
        "user_filter": "(objectClass=inetOrgPerson)",
        "email_attribute": "mail",
        "display_name_attribute": "displayName",
        "username_attribute": "uid",
        "member_of_attribute": "memberOf",
        "base_dn_example": "dc=example,dc=com",
        "bind_dn_example": "cn=readonly,dc=example,dc=com",
        "group_dn_example": "cn=moonwing-admins,ou=groups,dc=example,dc=com",
        "notes": "Works with OpenLDAP and other RFC-style LDAP directories.",
    },
    "ad": {
        "label": "Active Directory",
        "port": 636,
        "use_ssl": True,
        "start_tls": False,
        "user_filter": "(&(objectClass=user)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))",
        "email_attribute": "mail",
        "display_name_attribute": "displayName",
        "username_attribute": "sAMAccountName",
        "member_of_attribute": "memberOf",
        "base_dn_example": "dc=corp,dc=example,dc=com",
        "bind_dn_example": "CN=moonwing-readonly,OU=Service Accounts,DC=corp,DC=example,DC=com",
        "group_dn_example": "CN=Moonwing Admins,OU=Security Groups,DC=corp,DC=example,DC=com",
        "notes": "Uses common AD attributes and excludes disabled user accounts.",
    },
    "authentik": {
        "label": "Authentik LDAP Outpost",
        "port": 636,
        "use_ssl": True,
        "start_tls": False,
        "user_filter": "(objectClass=user)",
        "email_attribute": "mail",
        "display_name_attribute": "name",
        "username_attribute": "uid",
        "member_of_attribute": "memberOf",
        "base_dn_example": "dc=ldap,dc=goauthentik,dc=io",
        "bind_dn_example": "cn=ldapservice,ou=users,dc=ldap,dc=goauthentik,dc=io",
        "group_dn_example": "cn=moonwing-admins,ou=groups,dc=ldap,dc=goauthentik,dc=io",
        "notes": "Supported as a provider type but not preconfigured or connected.",
    },
}


class LdapSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class LdapUserEntry:
    dn: str
    email: str
    display_name: str
    group_dns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LdapSyncConfigData:
    default_role: str = "viewer"
    auto_disable_missing: bool = False
    role_group_dns: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class LdapSyncSummary:
    created: int = 0
    updated: int = 0
    disabled: int = 0
    skipped: int = 0
    conflicts: int = 0
    total_seen: int = 0


def _split_dns(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.replace("\r", "\n").split("\n") if part.strip()]


def _norm_dn(value: str | None) -> str:
    return (value or "").strip().lower()


def _norm_email(value: str | None) -> str:
    return (value or "").strip().lower()


def config_data_from_model(config: LdapConfig) -> LdapSyncConfigData:
    return LdapSyncConfigData(
        default_role=config.default_role,
        auto_disable_missing=config.auto_disable_missing,
        role_group_dns={
            "admin": _split_dns(config.admin_group_dns),
            "security_engineer": _split_dns(config.security_engineer_group_dns),
            "operator": _split_dns(config.operator_group_dns),
            "analyst": _split_dns(config.analyst_group_dns),
            "viewer": _split_dns(config.viewer_group_dns),
        },
    )


def role_for_groups(group_dns: list[str], config: LdapSyncConfigData) -> str:
    default_role = config.default_role if config.default_role in VALID_ROLES else "viewer"
    normalized_groups = {_norm_dn(group) for group in group_dns}
    for role in ROLE_PRIORITY:
        configured = {_norm_dn(group) for group in config.role_group_dns.get(role, [])}
        if configured and normalized_groups.intersection(configured):
            return role
    return default_role


def _find_existing_user(session: Session, *, email: str, dn: str) -> User | None:
    user = (
        session.query(User)
        .filter(User.auth_source == "ldap", User.external_id == dn)
        .first()
    )
    if user:
        return user
    return session.query(User).filter(User.email == email).first()


def sync_ldap_entries(
    session: Session,
    entries: list[LdapUserEntry],
    config: LdapSyncConfigData,
    *,
    actor_user_id: UUID | None = None,
    dry_run: bool = False,
) -> LdapSyncSummary:
    summary = LdapSyncSummary(total_seen=len(entries))
    seen_external_ids: set[str] = set()

    for entry in entries:
        email = _norm_email(entry.email)
        dn = entry.dn.strip()
        if not email or not dn:
            summary.skipped += 1
            continue

        seen_external_ids.add(dn)
        role = role_for_groups(entry.group_dns, config)
        display_name = entry.display_name.strip() or email
        existing = _find_existing_user(session, email=email, dn=dn)

        if existing is not None and existing.auth_source not in {"ldap", "local"}:
            summary.conflicts += 1
            continue
        if existing is not None and existing.auth_source == "local" and existing.external_id != dn:
            summary.conflicts += 1
            continue

        if existing is None:
            summary.created += 1
            if not dry_run:
                user = User(
                    email=email,
                    display_name=display_name,
                    password_hash=None,
                    role=role,
                    status="active",
                    is_service_account=False,
                    is_bootstrap=False,
                    must_change_password=False,
                    auth_source="ldap",
                    external_id=dn,
                    ldap_dn=dn,
                    last_synced_at=datetime.now(timezone.utc),
                )
                session.add(user)
                audit(session, action="ldap_user_create", resource_type="user", actor_user_id=actor_user_id, resource_id=email, metadata={"role": role})
            continue

        changed = (
            existing.email != email
            or existing.display_name != display_name
            or existing.role != role
            or existing.status != "active"
            or existing.auth_source != "ldap"
            or existing.external_id != dn
            or existing.ldap_dn != dn
        )
        if changed:
            summary.updated += 1
            if not dry_run:
                old_role = existing.role
                old_status = existing.status
                existing.email = email
                existing.display_name = display_name
                existing.role = role
                existing.status = "active"
                existing.auth_source = "ldap"
                existing.external_id = dn
                existing.ldap_dn = dn
                existing.password_hash = None
                existing.last_synced_at = datetime.now(timezone.utc)
                audit(
                    session,
                    action="ldap_user_update",
                    resource_type="user",
                    actor_user_id=actor_user_id,
                    resource_id=email,
                    metadata={"old_role": old_role, "new_role": role, "old_status": old_status, "new_status": "active"},
                )
        elif not dry_run:
            existing.last_synced_at = datetime.now(timezone.utc)

    if config.auto_disable_missing:
        query = session.query(User).filter(User.auth_source == "ldap", User.status == "active")
        for user in query.all():
            if user.external_id not in seen_external_ids:
                summary.disabled += 1
                if not dry_run:
                    user.status = "disabled"
                    user.last_synced_at = datetime.now(timezone.utc)
                    audit(session, action="ldap_user_disable_missing", resource_type="user", actor_user_id=actor_user_id, resource_id=user.email)

    if not dry_run:
        session.flush()
    return summary


def _first_attr(attrs: dict, name: str) -> str:
    value = attrs.get(name)
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def fetch_ldap_entries(config: LdapConfig) -> list[LdapUserEntry]:
    try:
        from ldap3 import ALL, Connection, Server, SUBTREE
    except ImportError as exc:
        raise LdapSyncError("ldap3 is not installed in the Moonwing environment") from exc

    password = ""
    if config.encrypted_bind_password:
        try:
            password = decrypt_api_key(config.encrypted_bind_password)
        except CryptoError as exc:
            raise LdapSyncError("LDAP bind password could not be decrypted") from exc

    server = Server(config.host, port=config.port, use_ssl=config.use_ssl, get_info=ALL)
    conn = Connection(
        server,
        user=config.bind_dn or None,
        password=password or None,
        auto_bind=False,
        receive_timeout=15,
    )
    try:
        if not conn.bind():
            raise LdapSyncError(f"LDAP bind failed: {conn.result.get('description', 'unknown error')}")
        if config.start_tls and not config.use_ssl:
            if not conn.start_tls():
                raise LdapSyncError(f"LDAP STARTTLS failed: {conn.result.get('description', 'unknown error')}")
        attributes = [
            config.email_attribute,
            config.display_name_attribute,
            config.username_attribute,
            config.member_of_attribute,
        ]
        if not conn.search(config.base_dn, config.user_filter, search_scope=SUBTREE, attributes=attributes):
            return []
        entries: list[LdapUserEntry] = []
        for raw in conn.entries:
            attrs = raw.entry_attributes_as_dict
            email = _first_attr(attrs, config.email_attribute)
            display_name = _first_attr(attrs, config.display_name_attribute) or _first_attr(attrs, config.username_attribute)
            groups = attrs.get(config.member_of_attribute) or []
            if not isinstance(groups, list):
                groups = [str(groups)]
            entries.append(LdapUserEntry(dn=str(raw.entry_dn), email=email, display_name=display_name, group_dns=[str(g) for g in groups]))
        return entries
    finally:
        conn.unbind()


def run_ldap_sync(session: Session, config: LdapConfig, *, actor_user_id: UUID | None = None, dry_run: bool = False) -> LdapSyncSummary:
    entries = fetch_ldap_entries(config)
    summary = sync_ldap_entries(session, entries, config_data_from_model(config), actor_user_id=actor_user_id, dry_run=dry_run)
    if not dry_run:
        config.last_sync_at = datetime.now(timezone.utc)
    return summary


def build_ldap_diagnostics(entries: list[LdapUserEntry], config: LdapSyncConfigData) -> dict:
    sample = None
    missing_email = 0
    missing_display_name = 0
    missing_groups = 0

    for entry in entries:
        role = role_for_groups(entry.group_dns, config)
        if not entry.email.strip():
            missing_email += 1
        if not entry.display_name.strip():
            missing_display_name += 1
        if not entry.group_dns:
            missing_groups += 1
        if sample is None and entry.email.strip():
            sample = {
                "dn": entry.dn,
                "email": entry.email.strip().lower(),
                "display_name": entry.display_name.strip() or entry.email.strip().lower(),
                "role": role,
                "group_count": len(entry.group_dns),
            }

    return {
        "bind_ok": True,
        "base_search_ok": True,
        "matched_users": len(entries),
        "missing_email": missing_email,
        "missing_display_name": missing_display_name,
        "missing_groups": missing_groups,
        "sample_user": sample,
    }


def preview_ldap_sync(session: Session, entries: list[LdapUserEntry], config: LdapSyncConfigData) -> dict:
    summary = LdapSyncSummary(total_seen=len(entries))
    rows: list[dict] = []
    seen_external_ids: set[str] = set()

    for entry in entries:
        email = _norm_email(entry.email)
        dn = entry.dn.strip()
        role = role_for_groups(entry.group_dns, config)
        display_name = entry.display_name.strip() or email
        if not email or not dn:
            summary.skipped += 1
            rows.append({
                "action": "skip",
                "email": email or "(missing email)",
                "display_name": display_name or "(missing display name)",
                "role": role,
                "reason": "Missing email or DN",
            })
            continue

        seen_external_ids.add(dn)
        existing = _find_existing_user(session, email=email, dn=dn)
        row = {
            "email": email,
            "display_name": display_name,
            "role": role,
            "dn": dn,
            "current_role": existing.role if existing else "",
            "current_status": existing.status if existing else "",
            "reason": "",
        }

        if existing is not None and existing.auth_source == "local" and existing.external_id != dn:
            summary.conflicts += 1
            row["action"] = "conflict"
            row["reason"] = "Email already belongs to a local user"
        elif existing is None:
            summary.created += 1
            row["action"] = "create"
            row["reason"] = "New LDAP user"
        else:
            changed = (
                existing.email != email
                or existing.display_name != display_name
                or existing.role != role
                or existing.status != "active"
                or existing.auth_source != "ldap"
                or existing.external_id != dn
                or existing.ldap_dn != dn
            )
            if changed:
                summary.updated += 1
                row["action"] = "update"
                row["reason"] = "Role, profile, or status will change"
            else:
                row["action"] = "unchanged"
                row["reason"] = "Already in sync"
        rows.append(row)

    if config.auto_disable_missing:
        for user in session.query(User).filter(User.auth_source == "ldap", User.status == "active").all():
            if user.external_id not in seen_external_ids:
                summary.disabled += 1
                rows.append({
                    "action": "disable",
                    "email": user.email,
                    "display_name": user.display_name,
                    "role": user.role,
                    "dn": user.ldap_dn or user.external_id or "",
                    "current_role": user.role,
                    "current_status": user.status,
                    "reason": "LDAP user not returned by current query",
                })

    action_order = {"conflict": 0, "create": 1, "update": 2, "disable": 3, "skip": 4, "unchanged": 5}
    rows.sort(key=lambda row: (action_order.get(row["action"], 99), row["email"]))
    return {"summary": summary, "rows": rows}

