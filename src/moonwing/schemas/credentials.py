from pydantic import BaseModel, ConfigDict

from moonwing.core.providers import EndpointType, Provider


class CredentialSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')

    credential_id: str
    provider: Provider
    model: str
    endpoint_type: EndpointType = EndpointType.HOSTED
    ownership_scope: str = 'user'
