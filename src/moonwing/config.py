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
    # At API/worker startup, optionally spawn each found CLI with --version / -V / --help
    # (short timeout, non-interactive) to confirm the binary actually runs.
    cli_boot_smoke: bool = True
    cli_boot_smoke_timeout_seconds: float = 4.0
    workspace_dir: str = '/var/lib/moonwing/workspace/runs'
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

    # ── SIEM / observability ───────────────────────────────────────────
    siem_enabled: bool = False
    siem_http_url: str = ''
    siem_http_headers_json: str = ''
    siem_http_timeout_seconds: float = 3.0
    log_json_to_stdout: bool = False
    update_repo_path: str = ''
    update_git_remote: str = 'origin'
    update_git_branch: str = 'main'
    update_venv_python: str = ''
    update_github_repository: str = ''
    update_github_branch: str = ''
    update_systemd_units: str = ''
    update_pip_timeout_seconds: int = 600
    update_alembic_timeout_seconds: int = 300
    # JSON argv for host reboot from Updates (empty = hidden). Example:
    # '["sudo","/sbin/shutdown","-r","now"]'
    admin_reboot_argv_json: str = ''
