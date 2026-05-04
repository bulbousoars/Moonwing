from pydantic import ValidationError

from moonwing.core.findings import NormalizedFinding


class NormalizationError(ValueError):
    pass


def normalize_findings(raw_payload: dict) -> list[dict]:
    if not isinstance(raw_payload, dict):
        raise NormalizationError("raw payload must be an object")

    raw_findings = raw_payload.get("findings", [])
    if not isinstance(raw_findings, list):
        raise NormalizationError("raw payload findings must be a list")

    normalized = []
    for item in raw_findings:
        if not isinstance(item, dict):
            raise NormalizationError("each raw finding must be an object")
        if not item.get("title"):
            raise NormalizationError("raw finding title is required")

        try:
            finding = NormalizedFinding(
                title=item["title"],
                severity=item.get("severity", "unknown"),
                evidence_refs=item.get("evidence", []),
            )
        except ValidationError as exc:
            raise NormalizationError("raw finding failed validation") from exc
        normalized.append(finding.model_dump(mode="json"))
    return normalized
