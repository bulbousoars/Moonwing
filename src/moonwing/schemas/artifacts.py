from pydantic import BaseModel, ConfigDict


class ArtifactReference(BaseModel):
    model_config = ConfigDict(extra='forbid')

    bucket: str
    object_key: str


class ArtifactMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source_type: str
    source_location: str | None = None
    retrieved_at: str | None = None
    installed_at: str | None = None
    original_filepath: str | None = None
    current_filepath: str | None = None
    format: str | None = None
    checksum: str | None = None
