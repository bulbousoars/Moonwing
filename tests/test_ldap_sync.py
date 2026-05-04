from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from moonwing.db.base import Base
from moonwing.db.models import AuditEvent, User
from moonwing.services.ldap_sync import LdapUserEntry, LdapSyncConfigData, sync_ldap_entries


def make_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def test_ldap_sync_creates_and_updates_users_from_group_role_mappings():
    session = make_session()
    session.add(
        User(
            email="local@moonwing.local",
            display_name="Local Admin",
            role="admin",
            status="active",
            auth_source="local",
        )
    )
    session.add(
        User(
            email="old@example.com",
            display_name="Old LDAP User",
            role="viewer",
            status="active",
            auth_source="ldap",
            external_id="uid=old,ou=users,dc=example,dc=com",
            ldap_dn="uid=old,ou=users,dc=example,dc=com",
        )
    )
    session.commit()

    config = LdapSyncConfigData(
        default_role="viewer",
        auto_disable_missing=True,
        role_group_dns={
            "admin": ["cn=moonwing-admins,ou=groups,dc=example,dc=com"],
            "security_engineer": ["cn=moonwing-security,ou=groups,dc=example,dc=com"],
        },
    )
    entries = [
        LdapUserEntry(
            dn="uid=alice,ou=users,dc=example,dc=com",
            email="alice@example.com",
            display_name="Alice Example",
            group_dns=["cn=moonwing-security,ou=groups,dc=example,dc=com"],
        ),
        LdapUserEntry(
            dn="uid=bob,ou=users,dc=example,dc=com",
            email="bob@example.com",
            display_name="Bob Example",
            group_dns=[],
        ),
    ]

    summary = sync_ldap_entries(session, entries, config)

    assert summary.created == 2
    assert summary.updated == 0
    assert summary.disabled == 1
    alice = session.query(User).filter_by(email="alice@example.com").one()
    assert alice.display_name == "Alice Example"
    assert alice.role == "security_engineer"
    assert alice.status == "active"
    assert alice.auth_source == "ldap"
    assert alice.password_hash is None
    bob = session.query(User).filter_by(email="bob@example.com").one()
    assert bob.role == "viewer"
    old = session.query(User).filter_by(email="old@example.com").one()
    assert old.status == "disabled"
    local = session.query(User).filter_by(email="local@moonwing.local").one()
    assert local.status == "active"
    assert session.query(AuditEvent).count() == 3


def test_ldap_sync_dry_run_does_not_persist_changes():
    session = make_session()
    config = LdapSyncConfigData(default_role="analyst", auto_disable_missing=False, role_group_dns={})
    entries = [
        LdapUserEntry(
            dn="uid=charlie,ou=users,dc=example,dc=com",
            email="charlie@example.com",
            display_name="Charlie Example",
            group_dns=[],
        )
    ]

    summary = sync_ldap_entries(session, entries, config, dry_run=True)

    assert summary.created == 1
    assert session.query(User).filter_by(email="charlie@example.com").count() == 0
    assert session.query(AuditEvent).count() == 0



def test_provider_presets_explain_directory_defaults():
    from moonwing.services.ldap_sync import LDAP_PROVIDER_PRESETS

    assert LDAP_PROVIDER_PRESETS["ad"]["port"] == 636
    assert "userAccountControl" in LDAP_PROVIDER_PRESETS["ad"]["user_filter"]
    assert LDAP_PROVIDER_PRESETS["generic"]["user_filter"] == "(objectClass=inetOrgPerson)"
    assert LDAP_PROVIDER_PRESETS["authentik"]["member_of_attribute"] == "memberOf"
    assert "not preconfigured" in LDAP_PROVIDER_PRESETS["authentik"]["notes"].lower()


def test_build_ldap_diagnostics_reports_missing_attributes_and_sample_user():
    from moonwing.services.ldap_sync import build_ldap_diagnostics

    entries = [
        LdapUserEntry(
            dn="uid=alice,ou=users,dc=example,dc=com",
            email="alice@example.com",
            display_name="Alice Example",
            group_dns=["cn=moonwing-admins,ou=groups,dc=example,dc=com"],
        ),
        LdapUserEntry(
            dn="uid=missing,ou=users,dc=example,dc=com",
            email="",
            display_name="No Email",
            group_dns=[],
        ),
    ]
    config = LdapSyncConfigData(
        default_role="viewer",
        role_group_dns={"admin": ["cn=moonwing-admins,ou=groups,dc=example,dc=com"]},
    )

    diagnostics = build_ldap_diagnostics(entries, config)

    assert diagnostics["bind_ok"] is True
    assert diagnostics["matched_users"] == 2
    assert diagnostics["missing_email"] == 1
    assert diagnostics["missing_display_name"] == 0
    assert diagnostics["missing_groups"] == 1
    assert diagnostics["sample_user"]["email"] == "alice@example.com"
    assert diagnostics["sample_user"]["role"] == "admin"


def test_preview_ldap_sync_lists_create_update_disable_and_conflict_actions():
    session = make_session()
    session.add(
        User(
            email="local@example.com",
            display_name="Local User",
            role="viewer",
            status="active",
            auth_source="local",
        )
    )
    session.add(
        User(
            email="old@example.com",
            display_name="Old LDAP User",
            role="viewer",
            status="active",
            auth_source="ldap",
            external_id="uid=old,ou=users,dc=example,dc=com",
            ldap_dn="uid=old,ou=users,dc=example,dc=com",
        )
    )
    session.add(
        User(
            email="update@example.com",
            display_name="Old Name",
            role="viewer",
            status="disabled",
            auth_source="ldap",
            external_id="uid=update,ou=users,dc=example,dc=com",
            ldap_dn="uid=update,ou=users,dc=example,dc=com",
        )
    )
    session.commit()

    from moonwing.services.ldap_sync import preview_ldap_sync

    config = LdapSyncConfigData(
        default_role="viewer",
        auto_disable_missing=True,
        role_group_dns={"operator": ["cn=operators,ou=groups,dc=example,dc=com"]},
    )
    entries = [
        LdapUserEntry(
            dn="uid=new,ou=users,dc=example,dc=com",
            email="new@example.com",
            display_name="New User",
            group_dns=[],
        ),
        LdapUserEntry(
            dn="uid=update,ou=users,dc=example,dc=com",
            email="update@example.com",
            display_name="Updated Name",
            group_dns=["cn=operators,ou=groups,dc=example,dc=com"],
        ),
        LdapUserEntry(
            dn="uid=local,ou=users,dc=example,dc=com",
            email="local@example.com",
            display_name="Local Conflict",
            group_dns=[],
        ),
    ]

    preview = preview_ldap_sync(session, entries, config)
    actions = {row["email"]: row["action"] for row in preview["rows"]}

    assert preview["summary"].created == 1
    assert preview["summary"].updated == 1
    assert preview["summary"].disabled == 1
    assert preview["summary"].conflicts == 1
    assert actions["new@example.com"] == "create"
    assert actions["update@example.com"] == "update"
    assert actions["local@example.com"] == "conflict"
    assert actions["old@example.com"] == "disable"
