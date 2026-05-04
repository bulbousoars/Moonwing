import pytest

from moonwing.services.normalization import NormalizationError, normalize_findings


def test_normalize_findings_extracts_title_severity_and_evidence_refs():
    raw = {
        "findings": [
            {
                "title": "SQL injection",
                "severity": "high",
                "evidence": ["artifact://log-1"],
            }
        ]
    }

    findings = normalize_findings(raw)

    assert findings[0]["title"] == "SQL injection"
    assert findings[0]["severity"] == "high"
    assert findings[0]["evidence_refs"] == ["artifact://log-1"]


def test_normalize_findings_rejects_missing_title():
    with pytest.raises(NormalizationError):
        normalize_findings({"findings": [{"severity": "high", "evidence": []}]})


def test_normalize_findings_rejects_non_object_finding_items():
    with pytest.raises(NormalizationError):
        normalize_findings({"findings": ["not-a-finding"]})


def test_normalize_findings_rejects_non_list_findings_container():
    with pytest.raises(NormalizationError):
        normalize_findings({"findings": {"title": "SQL injection"}})


def test_normalize_findings_rejects_non_object_payload():
    with pytest.raises(NormalizationError):
        normalize_findings(["not-a-payload"])


def test_normalize_findings_wraps_model_validation_errors():
    with pytest.raises(NormalizationError):
        normalize_findings({"findings": [{"title": "Bad severity", "severity": "severe"}]})
