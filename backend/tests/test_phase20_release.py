"""Phase 20 release guards: the documented configuration matches the code, and the example file carries no secret."""

import re
from pathlib import Path

from app.core.config import Settings

BACKEND = Path(__file__).resolve().parent.parent
EXAMPLE = (BACKEND / ".env.example").read_text()
# Settings that are read under another name (a provider's own variable) or are internal, and so are not KIRANA_-prefixed examples.
NOT_DOCUMENTED = {"app_name", "app_version", "csrf_cookie_name", "session_cookie_name", "allowed_image_types", "ai_api_key", "image_analysis_api_key", "upcitemdb_api_key"}


def test_every_setting_is_documented_in_the_env_example():
    documented = set(re.findall(r"^#?\s*KIRANA_([A-Z0-9_]+)=", EXAMPLE, re.M))
    missing = sorted(f for f in Settings.model_fields if f not in NOT_DOCUMENTED and f.upper() not in documented)
    assert not missing, f"add these to backend/.env.example: {missing}"


def test_the_env_example_names_only_real_settings():
    documented = set(re.findall(r"^#?\s*KIRANA_([A-Z0-9_]+)=", EXAMPLE, re.M))
    known = {f.upper() for f in Settings.model_fields} | {"DEV_OWNER_PASSWORD"}
    unknown = sorted(n for n in documented if n not in known and not n.startswith("INTEGRATION_"))
    assert not unknown, unknown


def test_the_env_example_contains_no_secret_values():
    for line in EXAMPLE.splitlines():
        if re.match(r"^#?\s*[A-Z_]*(_KEY|_SECRET|_TOKEN|_PASSWORD)=", line):
            value = line.split("=", 1)[1].strip()
            assert value in ("", "your_api_key_here") or value.startswith(("KIRANA_", "<")), line


def test_production_defaults_refuse_an_unsafe_start(monkeypatch):
    settings = Settings(environment="production")
    problems = settings.production_problems()
    assert any("SECRET_KEY" in p for p in problems) and any("FRONTEND_URL" in p for p in problems)


def test_every_nginx_location_that_sets_headers_also_includes_the_security_headers():
    """nginx drops server-level add_header in a location that has its own; the snippet must be included there (checked live with nginx in the follow-up QA)."""
    import re
    from pathlib import Path

    conf = (Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf").read_text()
    blocks = re.findall(r"location [^{]+\{(.*?)\n    \}", conf, re.S)
    assert blocks
    for block in blocks:
        if "add_header" in block:
            assert "include /etc/nginx/snippets/security-headers.conf;" in block, block


def test_a_hosting_platforms_database_url_is_understood(monkeypatch):
    for name in ("KIRANA_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host.example/db?sslmode=require")
    assert Settings(_env_file=None).database_url == "postgresql+psycopg://u:p@host.example/db?sslmode=require"
    monkeypatch.setenv("KIRANA_DATABASE_URL", "sqlite:///./mine.db")  # the explicit setting always wins
    assert Settings(_env_file=None).database_url == "sqlite:///./mine.db"


def test_the_cron_endpoint_exists_only_with_a_secret_and_needs_it(real_client, monkeypatch):
    client = real_client()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert client.get("/api/cron/tick").status_code == 404  # not configured: it does not exist
    monkeypatch.setenv("CRON_SECRET", "s3cret-value-for-tests")
    assert client.get("/api/cron/tick").status_code == 401
    assert client.get("/api/cron/tick", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/cron/tick", headers={"Authorization": "Bearer s3cret-value-for-tests"})
    assert ok.status_code == 200 and set(ok.json()) == {"ran", "failed"}


def test_the_vercel_requirements_file_is_a_copy_of_the_backends():
    root = (BACKEND.parent / "requirements.txt").read_text().splitlines()[1:]
    assert root == (BACKEND / "requirements.txt").read_text().splitlines()


def test_vercel_defaults_apply_only_on_vercel_and_never_override_the_operator():
    from app.core.hosting import apply_vercel_defaults

    plain: dict[str, str] = {}
    apply_vercel_defaults(plain)
    assert plain == {}  # not on Vercel: nothing is touched
    on_vercel = {"VERCEL": "1", "VERCEL_PROJECT_PRODUCTION_URL": "shop.vercel.app", "DATABASE_URL": "postgres://u:pw@h/db", "KIRANA_LOG_FORMAT": "text"}
    apply_vercel_defaults(on_vercel)
    assert on_vercel["KIRANA_ENVIRONMENT"] == "production" and on_vercel["KIRANA_FRONTEND_URL"] == "https://shop.vercel.app"
    assert on_vercel["KIRANA_LOG_FORMAT"] == "text"  # the operator's value wins
    assert len(on_vercel["KIRANA_SECRET_KEY"]) >= 32 and "pw" not in on_vercel["KIRANA_SECRET_KEY"]
    again = dict(on_vercel)
    apply_vercel_defaults(again)
    assert again == on_vercel  # stable
    own = {"VERCEL": "1", "DATABASE_URL": "postgres://u:pw@h/db", "KIRANA_SECRET_KEY": "x" * 40}
    apply_vercel_defaults(own)
    assert own["KIRANA_SECRET_KEY"] == "x" * 40


def test_a_custom_prefixed_database_url_is_found_and_the_pooled_one_preferred():
    from app.core.hosting import apply_vercel_defaults, find_database_url

    env = {"STORAGE_URL_UNPOOLED": "postgresql://u:p@direct/db", "STORAGE_URL": "postgresql://u:p@pooler/db", "UNRELATED": "x"}
    assert find_database_url(env) == ("STORAGE_URL", "postgresql://u:p@pooler/db")
    assert find_database_url({"DATABASE_URL": "postgres://a", "STORAGE_URL": "postgres://b"}) == ("DATABASE_URL", "postgres://a")
    assert find_database_url({"X": "y"}) is None
    on_vercel = {"VERCEL": "1", **env}
    apply_vercel_defaults(on_vercel)
    assert on_vercel["KIRANA_DATABASE_URL"] == "postgresql://u:p@pooler/db" and len(on_vercel["KIRANA_SECRET_KEY"]) >= 32
