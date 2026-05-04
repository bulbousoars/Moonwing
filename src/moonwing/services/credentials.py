from dataclasses import dataclass

from moonwing.core.providers import EndpointType, Provider


class SelectionError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedRunSelection:
    provider: Provider
    model: str
    credential_id: str
    endpoint_type: EndpointType
    ownership_scope: str


def _coerce_provider(value: str | None) -> Provider | None:
    if value is None:
        return None
    try:
        return Provider(value)
    except ValueError as exc:
        raise SelectionError(f'provider {value!r} is not supported') from exc


def _coerce_endpoint_type(value: str | None) -> EndpointType:
    if value is None:
        return EndpointType.HOSTED
    try:
        return EndpointType(value)
    except ValueError as exc:
        raise SelectionError(f'endpoint_type {value!r} is not supported') from exc


def _pick_value(explicit_value, user_value, profile_value):
    if explicit_value is not None:
        return explicit_value
    if user_value is not None:
        return user_value
    return profile_value


def resolve_run_selection(
    *,
    explicit_provider: str | None,
    explicit_model: str | None,
    explicit_credential_id: str | None,
    explicit_endpoint_type: str | None = None,
    explicit_ownership_scope: str | None = None,
    profile_defaults: dict | None,
    user_defaults: dict | None,
) -> ResolvedRunSelection:
    profile_defaults = profile_defaults or {}
    user_defaults = user_defaults or {}

    provider_value = _pick_value(explicit_provider, user_defaults.get('provider'), profile_defaults.get('provider'))
    model = _pick_value(explicit_model, user_defaults.get('model'), profile_defaults.get('model'))
    credential_id = _pick_value(
        explicit_credential_id,
        user_defaults.get('credential_id'),
        profile_defaults.get('credential_id'),
    )
    endpoint_type_value = _pick_value(
        explicit_endpoint_type,
        user_defaults.get('endpoint_type'),
        profile_defaults.get('endpoint_type'),
    )
    ownership_scope = _pick_value(
        explicit_ownership_scope,
        user_defaults.get('ownership_scope'),
        profile_defaults.get('ownership_scope'),
    ) or 'user'

    if not provider_value or not model or not credential_id:
        raise SelectionError('provider, model, and credential_id must resolve to non-empty values')

    provider = _coerce_provider(provider_value)
    endpoint_type = _coerce_endpoint_type(endpoint_type_value)

    allow_shared_credentials = bool(profile_defaults.get('allow_shared_credentials', False))
    allowed_scopes = profile_defaults.get('allowed_ownership_scopes', ['user'])
    allowed_providers = profile_defaults.get('allowed_providers')
    allowed_models = profile_defaults.get('allowed_models')
    allow_local_backends = bool(profile_defaults.get('allow_local_backends', False))

    if ownership_scope not in allowed_scopes:
        raise SelectionError(f'ownership scope {ownership_scope!r} is not allowed by the runtime profile')

    if ownership_scope == 'shared' and not allow_shared_credentials:
        raise SelectionError('shared credentials are not allowed by the runtime profile')

    if allowed_providers and provider.value not in allowed_providers:
        raise SelectionError(f'provider {provider.value!r} is not allowed by the runtime profile')

    if allowed_models and model not in allowed_models:
        raise SelectionError(f'model {model!r} is not allowed by the runtime profile')

    if endpoint_type == EndpointType.SELF_HOSTED and not allow_local_backends:
        raise SelectionError('self-hosted backends are not allowed by the runtime profile')

    return ResolvedRunSelection(
        provider=provider,
        model=model,
        credential_id=credential_id,
        endpoint_type=endpoint_type,
        ownership_scope=ownership_scope,
    )
