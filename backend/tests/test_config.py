from app.core.config import Settings


def test_defaults_are_safe_for_local_development():
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.is_production is False
    assert settings.cors_origins == ["http://localhost:5173"]


def test_cors_origins_accepts_comma_separated_string(monkeypatch):
    monkeypatch.setenv("KIRANA_CORS_ORIGINS", "http://a.example, http://b.example ,")

    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["http://a.example", "http://b.example"]


def test_production_flag(monkeypatch):
    monkeypatch.setenv("KIRANA_ENVIRONMENT", "production")

    assert Settings(_env_file=None).is_production is True
