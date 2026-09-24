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
