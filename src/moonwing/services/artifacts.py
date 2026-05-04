from moonwing.schemas.artifacts import ArtifactMetadata, ArtifactReference


def build_sbom_metadata(**kwargs) -> dict:
    metadata = ArtifactMetadata(**kwargs)
    return metadata.model_dump(exclude_none=True)


def build_artifact_reference(*, bucket: str, object_key: str) -> dict:
    reference = ArtifactReference(bucket=bucket, object_key=object_key)
    return reference.model_dump()
