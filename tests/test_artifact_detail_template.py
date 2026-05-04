from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_artifact_detail_template_displays_core_artifact_fields():
    template = (ROOT / "src/moonwing/api/templates/artifact_detail.html").read_text(encoding="utf-8")

    assert "{{ artifact.id }}" in template
    assert "{{ artifact.artifact_type }}" in template
    assert "{{ artifact.object_key }}" in template
    assert "/targets/{{ artifact.target_id }}" in template
    assert "artifact.provenance" in template
