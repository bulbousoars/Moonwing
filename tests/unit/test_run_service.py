import pytest

from moonwing.services.runs import RunStateError, create_run_snapshot, transition_run_status


def test_create_run_snapshot_keeps_provider_model_and_policy_flags():
    snapshot = create_run_snapshot(
        provider='openai',
        model='gpt-5.2',
        credential_id='cred-1',
        runtime_profile_name='default-network',
        policy_flags={'allow_exploits': False},
    )

    assert snapshot['provider'] == 'openai'
    assert snapshot['model'] == 'gpt-5.2'
    assert snapshot['policy_flags']['allow_exploits'] is False


def test_create_run_snapshot_isolated_from_nested_policy_mutation():
    policy_flags = {'limits': {'budget': 10}}
    snapshot = create_run_snapshot(
        provider='openai',
        model='gpt-5.2',
        credential_id='cred-1',
        runtime_profile_name='default-network',
        policy_flags=policy_flags,
    )

    policy_flags['limits']['budget'] = 20

    assert snapshot['policy_flags']['limits']['budget'] == 10


def test_transition_run_status_allows_running_to_normalizing():
    assert transition_run_status('running', 'normalizing') == 'normalizing'


def test_transition_run_status_rejects_invalid_transition():
    with pytest.raises(RunStateError):
        transition_run_status('queued', 'completed')
