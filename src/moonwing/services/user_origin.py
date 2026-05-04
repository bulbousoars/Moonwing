from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UserOrigin:
    source_label: str
    role_source_label: str
    details: dict[str, str] = field(default_factory=dict)


def describe_user_origin(user) -> UserOrigin:
    if _get(user, "is_service_account"):
        return UserOrigin("Service", "Moonwing local", {})
    if _get(user, "is_bootstrap"):
        return UserOrigin("Bootstrap", "Moonwing bootstrap", {})

    provider = str(_get(user, "auth_provider", "") or _get(user, "auth_source", "") or "").lower()
    if provider == "oidc" or _get(user, "oidc_subject"):
        return UserOrigin(
            "OIDC",
            _role_source(user, fallback="OIDC default"),
            _details({"Issuer": _get(user, "oidc_issuer"), "Subject": _get(user, "oidc_subject")}),
        )

    if provider == "ldap" or _has_ldap_metadata(user):
        return UserOrigin(
            "LDAP",
            _role_source(user, fallback="LDAP group mapping"),
            _details(
                {
                    "Provider": _get(user, "ldap_provider_type") or _get(user, "ldap_provider"),
                    "DN": _get(user, "ldap_dn"),
                    "External ID": _get(user, "ldap_external_id") or _get(user, "ldap_uid") or _get(user, "external_id"),
                    "Last Sync": _get(user, "ldap_last_sync_at") or _get(user, "last_ldap_sync_at") or _get(user, "last_synced_at"),
                }
            ),
        )

    return UserOrigin("Moonwing", "Moonwing local", {})


def _role_source(user, *, fallback: str) -> str:
    raw = str(_get(user, "role_source", "") or _get(user, "ldap_role_source", "") or "").lower()
    labels = {
        "group_mapping": "LDAP group mapping",
        "ldap_group_mapping": "LDAP group mapping",
        "oidc_default": "OIDC default",
        "local": "Moonwing local",
        "manual": "Moonwing local",
    }
    return labels.get(raw, fallback)


def _has_ldap_metadata(user) -> bool:
    return any(
        _get(user, name)
        for name in (
            "ldap_dn",
            "ldap_external_id",
            "ldap_uid",
            "ldap_provider",
            "ldap_provider_type",
            "ldap_last_sync_at",
            "last_ldap_sync_at",
            "auth_source",
            "external_id",
            "last_synced_at",
        )
    )


def _details(values: dict[str, object]) -> dict[str, str]:
    return {key: str(value) for key, value in values.items() if value not in (None, "")}


def _get(user, name: str, default=None):
    if isinstance(user, dict):
        return user.get(name, default)
    return getattr(user, name, default)
