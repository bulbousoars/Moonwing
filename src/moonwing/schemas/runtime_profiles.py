from pydantic import BaseModel, ConfigDict, Field


class RuntimeProfileDefaults(BaseModel):
    model_config = ConfigDict(extra='forbid')

    provider: str | None = None
    model: str | None = None
    credential_id: str | None = None
    endpoint_type: str | None = None
    ownership_scope: str | None = None
    allow_shared_credentials: bool = False
    allow_local_backends: bool = False
    allowed_ownership_scopes: list[str] = Field(default_factory=lambda: ['user'])
    allowed_providers: list[str] = Field(default_factory=list)
    allowed_models: list[str] = Field(default_factory=list)
