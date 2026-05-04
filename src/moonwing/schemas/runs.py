from pydantic import BaseModel, ConfigDict, Field


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    job_family: str
    target_id: str | None = None
    runtime_profile_id: str
    provider: str
    model: str
    credential_id: str
    policy_flags: dict = Field(default_factory=dict)


class RunSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')

    provider: str
    model: str
    credential_id: str
    runtime_profile_name: str
    policy_flags: dict = Field(default_factory=dict)


class RunCreateResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    status: str
    job_family: str
    target_id: str | None = None
    runtime_profile_id: str
    snapshot: RunSnapshot
