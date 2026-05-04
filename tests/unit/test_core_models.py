import pytest
from pydantic import ValidationError

from moonwing.core.findings import FindingSeverity, NormalizedFinding
from moonwing.core.job_types import JobFamily, SourceInputKind
from moonwing.core.providers import EndpointType, Provider, ProviderSelection
from moonwing.core.runtime_profiles import RuntimeProfile


def test_supported_job_families_are_stable():
    assert JobFamily.NETWORK_SCAN.value == 'network_scan'
    assert JobFamily.SOURCE_HUNT.value == 'source_hunt'
    assert SourceInputKind.SBOM.value == 'sbom'


def test_provider_selection_keeps_backend_and_model_separate():
    selection = ProviderSelection(provider='openai', model='gpt-5.2', endpoint_type='hosted')

    assert selection.provider == Provider.OPENAI
    assert selection.model == 'gpt-5.2'
    assert selection.endpoint_type == EndpointType.HOSTED


def test_runtime_profile_rejects_zero_concurrency():
    with pytest.raises(ValidationError):
        RuntimeProfile(name='default', max_concurrency=0)


def test_domain_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        ProviderSelection(
            provider='openai',
            model='gpt-5.2',
            endpoint_type='hosted',
            extra_flag=True,
        )


def test_normalized_finding_uses_constrained_severity():
    finding = NormalizedFinding(title='SQL injection', severity='high')

    assert finding.severity == FindingSeverity.HIGH

    with pytest.raises(ValidationError):
        NormalizedFinding(title='Bad severity', severity='severe')
