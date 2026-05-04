from moonwing.services.directory_config import suggest_ldap_config


def test_active_directory_preset_derives_base_dn_and_search_defaults():
    config = suggest_ldap_config(provider_type="active_directory", domain="branch.corp.example.local")

    assert config.base_dn == "DC=branch,DC=corp,DC=example,DC=local"
    assert config.user_filter == "(&(objectCategory=person)(objectClass=user)(!(objectClass=computer)))"
    assert config.username_attribute == "sAMAccountName"
    assert "user@branch.corp.example.local" in config.bind_username_examples
    assert config.group_membership_attribute == "memberOf"


def test_authentik_preset_uses_authentik_ldap_base_and_bind_guidance():
    config = suggest_ldap_config(provider_type="authentik", domain="corp.example.com")

    assert config.base_dn == "dc=ldap,dc=corp,dc=example,dc=com"
    assert config.user_filter == "(objectClass=user)"
    assert config.username_attribute == "cn"
    assert "cn=ldap-bind,ou=users,dc=ldap,dc=corp,dc=example,dc=com" in config.bind_username_examples
    assert "Use the Authentik LDAP outpost host and a service account with search permission." in config.setup_notes


def test_custom_base_dn_overrides_provider_derivation():
    config = suggest_ldap_config(
        provider_type="openldap",
        domain="example.test",
        base_dn="ou=people,dc=example,dc=test",
    )

    assert config.base_dn == "ou=people,dc=example,dc=test"
    assert config.user_search_base == "ou=people,dc=example,dc=test"
