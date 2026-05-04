from pydantic import BaseModel, ConfigDict, Field


class RuntimeProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    allow_exploits: bool = False
    max_concurrency: int = Field(default=1, ge=1)
