from __future__ import annotations

from enum import StrEnum


class JobFamily(StrEnum):
    NETWORK_SCAN = "network_scan"
    SOURCE_HUNT = "source_hunt"


class SourceInputKind(StrEnum):
    REPO = "repo"
    URL = "url"
    PATH = "path"
    SBOM = "sbom"
    BINARY = "binary"
