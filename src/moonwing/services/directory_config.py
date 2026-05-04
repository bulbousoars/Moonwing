from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LdapSuggestedConfig:
    provider_type: str
    base_dn: str
    user_search_base: str
    group_search_base: str
    user_filter: str
    group_filter: str
    username_attribute: str
    email_attribute: str
    display_name_attribute: str
    group_membership_attribute: str
    bind_username_examples: list[str]
    setup_notes: list[str]


def suggest_ldap_config(
    *,
    provider_type: str,
    domain: str = "",
    base_dn: str = "",
) -> LdapSuggestedConfig:
    provider = _normalize_provider(provider_type)
    clean_domain = domain.strip().lower()
    resolved_base_dn = base_dn.strip() or _derive_base_dn(provider, clean_domain)

    if provider == "active_directory":
        return LdapSuggestedConfig(
            provider_type=provider,
            base_dn=resolved_base_dn,
            user_search_base=resolved_base_dn,
            group_search_base=resolved_base_dn,
            user_filter="(&(objectCategory=person)(objectClass=user)(!(objectClass=computer)))",
            group_filter="(objectClass=group)",
            username_attribute="sAMAccountName",
            email_attribute="mail",
            display_name_attribute="displayName",
            group_membership_attribute="memberOf",
            bind_username_examples=_ad_bind_examples(clean_domain, resolved_base_dn),
            setup_notes=[
                "Use LDAPS on port 636 when possible.",
                "A UPN bind such as user@domain is usually easier than a full DN.",
                "Start with the domain root as the base DN, then narrow search bases after the test passes.",
            ],
        )

    if provider == "authentik":
        return LdapSuggestedConfig(
            provider_type=provider,
            base_dn=resolved_base_dn,
            user_search_base=resolved_base_dn,
            group_search_base=resolved_base_dn,
            user_filter="(objectClass=user)",
            group_filter="(objectClass=group)",
            username_attribute="cn",
            email_attribute="mail",
            display_name_attribute="name",
            group_membership_attribute="memberOf",
            bind_username_examples=[f"cn=ldap-bind,ou=users,{resolved_base_dn}"],
            setup_notes=[
                "Use the Authentik LDAP outpost host and a service account with search permission.",
                "If bind fails but the host is reachable, verify the provider authentication flow and outpost logs.",
                "Authentik LDAP commonly uses a base DN under dc=ldap for provider-backed directories.",
            ],
        )

    if provider == "openldap":
        return LdapSuggestedConfig(
            provider_type=provider,
            base_dn=resolved_base_dn,
            user_search_base=resolved_base_dn,
            group_search_base=resolved_base_dn,
            user_filter="(objectClass=inetOrgPerson)",
            group_filter="(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames)(objectClass=posixGroup))",
            username_attribute="uid",
            email_attribute="mail",
            display_name_attribute="cn",
            group_membership_attribute="memberOf",
            bind_username_examples=[f"cn=ldap-bind,{resolved_base_dn}", f"uid=ldap-bind,{resolved_base_dn}"],
            setup_notes=[
                "Use a full DN for the bind username unless your server explicitly supports short names.",
                "If groups do not resolve, enable memberOf overlay or configure group member attribute mapping.",
            ],
        )

    return LdapSuggestedConfig(
        provider_type=provider,
        base_dn=resolved_base_dn,
        user_search_base=resolved_base_dn,
        group_search_base=resolved_base_dn,
        user_filter="(objectClass=*)",
        group_filter="(objectClass=groupOfNames)",
        username_attribute="uid",
        email_attribute="mail",
        display_name_attribute="cn",
        group_membership_attribute="memberOf",
        bind_username_examples=[f"cn=ldap-bind,{resolved_base_dn}"] if resolved_base_dn else [],
        setup_notes=[
            "Use this mode when your directory does not match AD, OpenLDAP, or Authentik.",
            "Run the staged connection test before saving so Moonwing can identify the failing step.",
        ],
    )


def _normalize_provider(provider_type: str) -> str:
    aliases = {
        "ad": "active_directory",
        "active-directory": "active_directory",
        "active directory": "active_directory",
        "authentik ldap": "authentik",
        "generic": "other",
        "custom": "other",
    }
    raw = (provider_type or "other").strip().lower().replace(" ", "_")
    return aliases.get(raw, raw if raw in {"active_directory", "authentik", "openldap"} else "other")


def _derive_base_dn(provider: str, domain: str) -> str:
    if not domain:
        return ""
    parts = [part for part in domain.split(".") if part]
    if provider == "authentik":
        parts = ["ldap", *parts]
    prefix = "DC" if provider == "active_directory" else "dc"
    return ",".join(f"{prefix}={part}" for part in parts)


def _ad_bind_examples(domain: str, base_dn: str) -> list[str]:
    examples = []
    if domain:
        examples.append(f"user@{domain}")
    if base_dn:
        examples.append(f"CN=Moonwing Sync,CN=Users,{base_dn}")
    return examples
