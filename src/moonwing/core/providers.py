from enum import Enum

from pydantic import BaseModel, ConfigDict


class Provider(str, Enum):
    OPENAI = 'openai'
    ANTHROPIC = 'anthropic'
    OPENROUTER = 'openrouter'
    OLLAMA = 'ollama'


class EndpointType(str, Enum):
    HOSTED = 'hosted'
    OPENAI_COMPATIBLE = 'openai_compatible'
    SELF_HOSTED = 'self_hosted'


class ProviderSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')

    provider: Provider
    model: str
    endpoint_type: EndpointType
