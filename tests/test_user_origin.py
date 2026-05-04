from types import SimpleNamespace

from moonwing.services.user_origin import describe_user_origin


def test_local_user_origin_and_role_source_are_moonwing():
    user = SimpleNamespace(
        auth_provider="local",
        is_service_account=False,
        is_bootstrap=False,
        role="operator",
    )

    origin = describe_user_origin(user)

    assert origin.source_label == "Moonwing"
    assert origin.role_source_label == "Moonwing local"


def test_oidc_user_origin_uses_issuer_and_default_role_source():
    user = SimpleNamespace(
        auth_provider="oidc",
        is_service_account=False,
        is_bootstrap=False,
        role="viewer",
        oidc_issuer="https://auth.example.test/application/o/moonwing/",
        oidc_subject="abc123",
    )

    origin = describe_user_origin(user)

    assert origin.source_label == "OIDC"
    assert origin.role_source_label == "OIDC default"
    assert origin.details["Issuer"] == "https://auth.example.test/application/o/moonwing/"
    assert origin.details["Subject"] == "abc123"


def test_ldap_user_origin_uses_directory_metadata_when_present():
    user = SimpleNamespace(
        auth_provider="ldap",
        is_service_account=False,
        is_bootstrap=False,
        role="admin",
        ldap_dn="cn=Alice,ou=users,dc=example,dc=test",
        ldap_provider_type="authentik",
        ldap_role_source="group_mapping",
        ldap_last_sync_at="2026-05-02T12:00:00",
    )

    origin = describe_user_origin(user)

    assert origin.source_label == "LDAP"
    assert origin.role_source_label == "LDAP group mapping"
    assert origin.details["Provider"] == "authentik"
    assert origin.details["DN"] == "cn=Alice,ou=users,dc=example,dc=test"
    assert origin.details["Last Sync"] == "2026-05-02T12:00:00"


def test_production_auth_source_fields_are_supported():
    user = SimpleNamespace(
        auth_source="ldap",
        is_service_account=False,
        is_bootstrap=False,
        role="viewer",
        ldap_dn="uid=bob,ou=users,dc=example,dc=test",
        external_id="uid:bob",
        last_synced_at="2026-05-02T13:00:00",
    )

    origin = describe_user_origin(user)

    assert origin.source_label == "LDAP"
    assert origin.details["External ID"] == "uid:bob"
    assert origin.details["Last Sync"] == "2026-05-02T13:00:00"


def test_service_and_bootstrap_sources_take_precedence():
    service = SimpleNamespace(auth_provider="oidc", is_service_account=True, is_bootstrap=False)
    bootstrap = SimpleNamespace(auth_provider="local", is_service_account=False, is_bootstrap=True)

    assert describe_user_origin(service).source_label == "Service"
    assert describe_user_origin(bootstrap).source_label == "Bootstrap"
