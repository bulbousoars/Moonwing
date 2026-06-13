from pydantic import ValidationError

from moonwing.core.findings import NormalizedFinding


class NormalizationError(ValueError):
    pass


DETAIL_FIELDS = (
    "product",
    "affected_hosts",
    "affected_ports",
    "description",
    "impact",
    "remediation",
    "confidence",
    "references",
    "source_file",
    "concern",
    "repo_ref",
)


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
                details=_extract_details(item),
            )
        except ValidationError as exc:
            raise NormalizationError("raw finding failed validation") from exc
        normalized.append(finding.model_dump(mode="json"))
    return normalized


def _extract_details(item: dict) -> dict:
    details = item.get("details")
    if isinstance(details, dict):
        extracted = {key: value for key, value in details.items() if value not in (None, "", [], {})}
    else:
        extracted = {}

    for key in DETAIL_FIELDS:
        value = item.get(key)
        if value not in (None, "", [], {}):
            extracted[key] = value

    return extracted
