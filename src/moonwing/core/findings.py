from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FindingSeverity(str, Enum):
    CRITICAL = 'critical'
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'
    INFO = 'info'
    UNKNOWN = 'unknown'


class NormalizedFinding(BaseModel):
    model_config = ConfigDict(extra='forbid')

    title: str
    severity: FindingSeverity = FindingSeverity.UNKNOWN
    evidence_refs: list[str] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)
