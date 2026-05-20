from moonwing.services.permissions import can, permission_for_request, require_role


def test_admin_can_perform_privileged_actions():
    assert can("admin", "manage_users")
    assert can("admin", "manage_credentials")
    assert can("admin", "launch_scan")
    assert can("admin", "view")


def test_operator_can_launch_scans_but_not_manage_users():
    assert can("operator", "launch_scan")
    assert can("operator", "manage_targets")
    assert not can("operator", "manage_users")


def test_viewer_is_read_only():
    assert can("viewer", "view")
    assert not can("viewer", "launch_scan")


def test_only_admin_can_system_updates():
    assert can("admin", "system_updates")
    assert not can("viewer", "system_updates")
    assert not can("security_engineer", "system_updates")


def test_require_role_raises_for_missing_permission():
    try:
        require_role("viewer", "launch_scan")
    except PermissionError as exc:
        assert "launch_scan" in str(exc)
    else:
        raise AssertionError("viewer should not be able to launch scans")


def test_api_paths_map_to_least_privilege_permissions():
    assert permission_for_request("/api/runs", "GET") == "view"
    assert permission_for_request("/api/runs", "POST") == "launch_scan"
    assert permission_for_request("/api/credentials/test", "POST") == "manage_credentials"
    assert permission_for_request("/api/users", "GET") == "manage_users"
    assert permission_for_request("/api/system/updates/status", "GET") == "system_updates"
    assert permission_for_request("/api/system/host-ai-tools", "GET") == "system_updates"
    assert permission_for_request("/findings", "GET") is None
