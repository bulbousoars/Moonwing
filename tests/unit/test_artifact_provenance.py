import pytest
from pydantic import ValidationError

from moonwing.services.artifacts import build_artifact_reference, build_sbom_metadata


def test_sbom_metadata_keeps_source_and_file_paths():
    metadata = build_sbom_metadata(
        source_type='downloaded_url',
        source_location='https://example.com/sbom.json',
        retrieved_at='2026-04-21T18:00:00Z',
        installed_at='2026-04-20T10:00:00Z',
        original_filepath='/tmp/build/sbom.json',
        current_filepath='/opt/app/sbom.json',
        format='cyclonedx',
    )

    assert metadata['source_location'] == 'https://example.com/sbom.json'
    assert metadata['original_filepath'] == '/tmp/build/sbom.json'
    assert metadata['current_filepath'] == '/opt/app/sbom.json'


def test_build_sbom_metadata_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        build_sbom_metadata(
            source_type='downloaded_url',
            source_location='https://example.com/sbom.json',
            format='cyclonedx',
            extra_field='unexpected',
        )


def test_build_artifact_reference_returns_bucket_and_object_key():
    reference = build_artifact_reference(bucket='moonwing-artifacts', object_key='runs/123/sbom.json')

    assert reference == {
        'bucket': 'moonwing-artifacts',
        'object_key': 'runs/123/sbom.json',
    }
