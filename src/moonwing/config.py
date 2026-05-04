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
    gemini_cli_binary: str = 'gemini'
    workspace_dir: str = '/mnt/storage/moonwing/workspace/runs'
    session_secret: str = 'moonwing-dev-session-secret-change-me'
    bootstrap_admin_email: str = 'admin'
    bootstrap_admin_password: str = 'admin'
    bootstrap_admin_display_name: str = 'Bootstrap Admin'
    oidc_authorization_endpoint: str = ''
    oidc_token_endpoint: str = ''
    oidc_userinfo_endpoint: str = ''
    oidc_issuer: str = ''
    oidc_client_id: str = ''
    oidc_client_secret: str = ''
    oidc_redirect_uri: str = ''
    oidc_scope: str = 'openid email profile'
    oidc_default_role: str = 'viewer'
    sensor_enrollment_token: str = ''
