# Moonwing Navigation Permission Guide

This document records the navigation audit pattern for future Moonwing UI changes.

## Rule

Global navigation visibility must be based on Moonwing permissions, not identity source.

Do not show or hide navigation based on `auth_source` values such as `local`, `ldap`, `ad`, or `authentik`. A user synced from LDAP with the `admin` role should see the same admin navigation as a local admin. A local viewer should not see privileged navigation just because the account is local.

## Current Source Of Truth

Permissions are defined in `src/moonwing/services/permissions.py`.

Route enforcement for HTML views generally happens in `src/moonwing/api/routes/dashboard.py` through `_require(request, '<permission>')`. The global sidebar receives `nav_permissions` from `_render()`, which computes permissions with the same `can(role, permission)` helper used by route enforcement.

## Current Sidebar Mapping

| Navigation item | Permission |
| --- | --- |
| Dashboard | `view` |
| Runs | `view` |
| Findings | `view` |
| Artifacts | `view` |
| Configuration | `view` |
| Launch Scan | `launch_scan` |
| Management | `manage_users` |
| State Machine | `view` |
| API Docs | `view` |

Management sub-pages such as Directory Sync and Email Notifications are linked from `/management/iam`, which itself requires `manage_users`. Their routes also independently require `manage_users`.

## Adding A New Navigation Item

1. Add or reuse a permission in `services/permissions.py`.
2. Enforce that permission in the target route with `_require()`.
3. Add the permission to the `nav_permissions` dictionary in `_render()` if the global sidebar or shared templates need it.
4. Wrap the template link in an `{% if nav.<permission_name> %}` guard.
5. Do not check `current_user.auth_source` or infer access from provider type.
6. Parse changed templates and run the test suite before restart.

## Security Boundary

Navigation hiding is not the security boundary. Route-level `_require()` checks are the boundary. The UI should reduce confusion, but every privileged page or action must still enforce its permission server-side.
