from moonwing.config import Settings


def test_settings_defaults_expose_core_service_names():
    settings = Settings()

    assert settings.app_name == "moonwing"
    assert settings.database_url.startswith("postgresql+")
    assert settings.object_storage_endpoint == "minio:9000"
    assert settings.queue_backend == "redis://redis:6379/0"
