from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='MOONWING_')

    app_name: str = 'moonwing'
    database_url: str = 'postgresql+psycopg://moonwing:moonwing@postgres:5432/moonwing'
    queue_backend: str = 'redis://redis:6379/0'
    object_storage_endpoint: str = 'minio:9000'
    object_storage_bucket: str = 'moonwing-artifacts'
    object_storage_access_key: str = 'minio'
    object_storage_secret_key: str = 'minioadmin'
    object_storage_secure: bool = False
    clearwing_binary: str = 'clearwing'
    claude_cli_binary: str = 'claude'
    codex_cli_binary: str = 'codex'
    workspace_dir: str = '/mnt/storage/moonwing/workspace/runs'
