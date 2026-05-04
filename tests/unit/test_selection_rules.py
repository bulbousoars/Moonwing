import pytest

from moonwing.core.providers import EndpointType, Provider
from moonwing.schemas.runtime_profiles import RuntimeProfileDefaults
from moonwing.services.credentials import ResolvedRunSelection, SelectionError, resolve_run_selection


def test_user_default_selection_prefers_explicit_model_over_profile_default():
    selection = resolve_run_selection(
        explicit_provider='anthropic',
        explicit_model='sonnet-4.6',
        explicit_credential_id='cred-user-1',
        profile_defaults={'provider': 'openai', 'model': 'gpt-5.2'},
        user_defaults={'provider': 'openai', 'model': 'gpt-5.2', 'credential_id': 'cred-user-2'},
    )

    assert selection.provider == Provider.ANTHROPIC
    assert selection.model == 'sonnet-4.6'
    assert selection.credential_id == 'cred-user-1'


def test_resolve_run_selection_falls_back_to_user_defaults_before_profile_defaults():
    selection = resolve_run_selection(
        explicit_provider=None,
        explicit_model=None,
        explicit_credential_id=None,
        profile_defaults={'provider': 'openai', 'model': 'gpt-5.2', 'credential_id': 'cred-profile-1'},
        user_defaults={'provider': 'openrouter', 'model': 'gpt-5.2', 'credential_id': 'cred-user-9'},
    )

    assert selection == ResolvedRunSelection(
        provider=Provider.OPENROUTER,
        model='gpt-5.2',
        credential_id='cred-user-9',
        endpoint_type=EndpointType.HOSTED,
        ownership_scope='user',
    )


def test_resolve_run_selection_rejects_invalid_provider_and_endpoint_type_values():
    with pytest.raises(SelectionError):
        resolve_run_selection(
            explicit_provider='bad-provider',
            explicit_model='gpt-5.2',
            explicit_credential_id='cred-user-1',
            profile_defaults={},
            user_defaults={},
        )

    with pytest.raises(SelectionError):
        resolve_run_selection(
            explicit_provider='openai',
            explicit_model='gpt-5.2',
            explicit_credential_id='cred-user-1',
            explicit_endpoint_type='bad-endpoint',
            profile_defaults={},
            user_defaults={},
        )


def test_resolve_run_selection_rejects_shared_credentials_when_profile_disallows_them():
    with pytest.raises(SelectionError):
        resolve_run_selection(
            explicit_provider='openai',
            explicit_model='gpt-5.2',
            explicit_credential_id='cred-shared-1',
            explicit_ownership_scope='shared',
            profile_defaults={'allow_shared_credentials': False, 'allowed_ownership_scopes': ['user', 'shared']},
            user_defaults={},
        )


def test_resolve_run_selection_rejects_disallowed_ownership_scope():
    with pytest.raises(SelectionError):
        resolve_run_selection(
            explicit_provider='openai',
            explicit_model='gpt-5.2',
            explicit_credential_id='cred-shared-1',
            explicit_ownership_scope='shared',
            profile_defaults={'allow_shared_credentials': True, 'allowed_ownership_scopes': ['user']},
            user_defaults={},
        )


def test_resolve_run_selection_keeps_explicit_endpoint_type():
    selection = resolve_run_selection(
        explicit_provider='ollama',
        explicit_model='llama3.1',
        explicit_credential_id='cred-local-1',
        explicit_endpoint_type='self_hosted',
        profile_defaults={'allow_shared_credentials': False, 'allow_local_backends': True},
        user_defaults={},
    )

    assert selection.endpoint_type == EndpointType.SELF_HOSTED


def test_resolve_run_selection_rejects_disallowed_provider_and_model():
    with pytest.raises(SelectionError):
        resolve_run_selection(
            explicit_provider='anthropic',
            explicit_model='sonnet-4.6',
            explicit_credential_id='cred-user-1',
            profile_defaults={'allowed_providers': ['openai'], 'allowed_models': ['gpt-5.2']},
            user_defaults={},
        )


def test_resolve_run_selection_rejects_self_hosted_backend_when_profile_disallows_it():
    with pytest.raises(SelectionError):
        resolve_run_selection(
            explicit_provider='ollama',
            explicit_model='llama3.1',
            explicit_credential_id='cred-local-1',
            explicit_endpoint_type='self_hosted',
            profile_defaults={'allow_local_backends': False},
            user_defaults={},
        )


def test_user_cannot_use_unapproved_shared_credential():
    """Shared credentials require both allow_shared_credentials=True AND
    'shared' in allowed_ownership_scopes. Missing either should fail."""
    # Case 1: allow_shared_credentials is False
    with pytest.raises(SelectionError, match='shared credentials are not allowed'):
        resolve_run_selection(
            explicit_provider='openai',
            explicit_model='gpt-5.2',
            explicit_credential_id='cred-shared-platform',
            explicit_ownership_scope='shared',
            profile_defaults={
                'allow_shared_credentials': False,
                'allowed_ownership_scopes': ['user', 'shared'],
            },
            user_defaults={},
        )

    # Case 2: allow_shared_credentials is True but 'shared' not in allowed scopes
    with pytest.raises(SelectionError, match='ownership scope'):
        resolve_run_selection(
            explicit_provider='openai',
            explicit_model='gpt-5.2',
            explicit_credential_id='cred-shared-platform',
            explicit_ownership_scope='shared',
            profile_defaults={
                'allow_shared_credentials': True,
                'allowed_ownership_scopes': ['user'],
            },
            user_defaults={},
        )

    # Case 3: Both enabled — should succeed
    selection = resolve_run_selection(
        explicit_provider='openai',
        explicit_model='gpt-5.2',
        explicit_credential_id='cred-shared-platform',
        explicit_ownership_scope='shared',
        profile_defaults={
            'allow_shared_credentials': True,
            'allowed_ownership_scopes': ['user', 'shared'],
        },
        user_defaults={},
    )
    assert selection.ownership_scope == 'shared'
    assert selection.credential_id == 'cred-shared-platform'


def test_runtime_profile_defaults_schema_can_drive_selection_rules():
    profile_defaults = RuntimeProfileDefaults(
        provider='openai',
        model='gpt-5.2',
        credential_id='cred-profile-1',
        endpoint_type='hosted',
        ownership_scope='user',
        allowed_providers=['openai'],
        allowed_models=['gpt-5.2'],
        allow_local_backends=False,
        allowed_ownership_scopes=['user'],
    )

    selection = resolve_run_selection(
        explicit_provider=None,
        explicit_model=None,
        explicit_credential_id=None,
        profile_defaults=profile_defaults.model_dump(exclude_none=True),
        user_defaults={},
    )

    assert selection.provider == Provider.OPENAI
    assert selection.model == 'gpt-5.2'
    assert selection.credential_id == 'cred-profile-1'
