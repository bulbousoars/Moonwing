from __future__ import annotations


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        "view",
        "launch_scan",
        "manage_targets",
        "manage_credentials",
        "manage_runtime_profiles",
        "manage_users",
        "manage_service_accounts",
        "manage_privileged_access",
    },
    "security_engineer": {
        "view",
        "launch_scan",
        "manage_targets",
        "manage_credentials",
        "manage_runtime_profiles",
    },
    "operator": {
        "view",
        "launch_scan",
        "manage_targets",
    },
    "analyst": {
        "view",
    },
    "viewer": {
        "view",
    },
    "service_account": {
        "view",
        "launch_scan",
    },
}

ASSIGNABLE_ROLES: list[dict[str, str]] = [
    {"value": "admin", "label": "Admin"},
    {"value": "security_engineer", "label": "Security Engineer"},
    {"value": "operator", "label": "Operator"},
    {"value": "analyst", "label": "Analyst"},
    {"value": "viewer", "label": "Viewer"},
]


def can(role: str | None, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role or "", set())


def require_role(role: str | None, permission: str) -> None:
    if not can(role, permission):
        raise PermissionError(f"role {role or 'anonymous'} lacks permission {permission}")


def permission_for_request(path: str, method: str) -> str | None:
    if not path.startswith("/api"):
        return None

    method = method.upper()
    if path.startswith("/api/credentials"):
        return "manage_credentials"
    if path.startswith("/api/runtime-profiles"):
        return "manage_runtime_profiles"
    if path.startswith("/api/users"):
        return "manage_users"
    if path.startswith("/api/targets"):
        return "manage_targets" if method not in {"GET", "HEAD", "OPTIONS"} else "view"
    if path.startswith("/api/runs"):
        return "launch_scan" if method not in {"GET", "HEAD", "OPTIONS"} else "view"
    if path.startswith("/api/findings") or path.startswith("/api/artifacts"):
        return "view"
    return "view"
