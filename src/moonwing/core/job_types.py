from enum import Enum


class JobFamily(str, Enum):
    NETWORK_SCAN = 'network_scan'
    SOURCE_HUNT = 'source_hunt'


class SourceInputKind(str, Enum):
    REPO = 'repo'
    LOCAL_SOURCE_TREE = 'local_source_tree'
    BINARY = 'binary'
    SBOM = 'sbom'
