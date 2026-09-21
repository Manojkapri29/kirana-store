"""Phase 8 security review as tests: no secret in Git, the frontend or its build; keys stay in the backend."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = ROOT / "frontend" / "src"
SECRET_WORDS = (
    "UPCITEMDB",
    "upcitemdb_api_key",
    "user_key",
    "key_type",
    "3scale",
    "SecretStr",
    "api_key",
    "API_KEY",
)


def frontend_files():
    return [
        p for p in FRONTEND_SRC.rglob("*") if p.is_file() and p.suffix in {".ts", ".tsx", ".css", ".html"}
    ]


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines() if line]


class TestNoSecretsInTheFrontend:
    def test_frontend_source_never_names_a_provider_key(self):
        offenders = [
            f"{p.relative_to(ROOT)}: {word}"
            for p in frontend_files()
            for word in SECRET_WORDS
            if word in p.read_text(errors="ignore")
        ]
        assert offenders == []

    def test_no_public_frontend_variable_carries_a_key(self):
        for name in (".env.example", ".env.local", ".env"):
            path = ROOT / "frontend" / name
            if path.exists():
                names = re.findall(r"^\s*(VITE_[A-Z0-9_]+)\s*=", path.read_text(), flags=re.M)
                assert not [n for n in names if re.search(r"KEY|SECRET|TOKEN|PASSWORD", n)], (name, names)

    def test_the_built_frontend_contains_no_key_or_provider_credential_words(self):
        dist = ROOT / "frontend" / "dist"
        if not dist.exists():
            pytest.skip("frontend not built")
        for path in dist.rglob("*"):
            if path.is_file() and path.suffix in {".js", ".html", ".css"}:
                text = path.read_text(errors="ignore")
                for word in ("UPCITEMDB", "user_key", "3scale", "your_api_key_here"):
                    assert word not in text, (path.name, word)

    def test_the_frontend_never_calls_an_outside_provider_directly(self):
        hosts = ("upcitemdb.com", "openfoodfacts.org")
        offenders = [
            str(p.relative_to(ROOT)) for p in frontend_files() if any(h in p.read_text() for h in hosts)
        ]
        assert offenders == []  # the browser only ever talks to this app's own API


class TestNoSecretsInGit:
    def test_env_files_are_ignored_and_the_example_is_committed(self):
        ignored = subprocess.run(
            ["git", "check-ignore", "backend/.env", "frontend/.env.local", "backend/data/kirana.db"],
            cwd=ROOT, capture_output=True, text=True,
        ).stdout.split()  # fmt: skip
        assert {"backend/.env", "frontend/.env.local", "backend/data/kirana.db"} <= set(ignored)
        names = {p.relative_to(ROOT).as_posix() for p in tracked_files()}
        assert "backend/.env.example" in names and "backend/.env" not in names

    def test_the_example_holds_only_placeholders(self):
        text = (ROOT / "backend" / ".env.example").read_text()
        assert "UPCITEMDB_API_KEY=your_api_key_here" in text
        for line in text.splitlines():
            if re.match(r"^\s*[A-Z_]*(KEY|SECRET|TOKEN(?!S)|PASSWORD)[A-Z_]*\s*=", line):
                value = line.split("=", 1)[1].strip()
                # A number or true/false is a setting that merely has "PASSWORD"/"TOKEN" in its name, not a secret.
                assert value in {"your_api_key_here", ""} or re.fullmatch(r"\d+|true|false", value), line

    def test_no_tracked_file_contains_something_shaped_like_a_real_key(self):
        pattern = re.compile(
            r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})"
        )
        hits = []
        for path in tracked_files():
            if path.suffix in {".png", ".ico", ".db", ".pdf", ".xlsx", ".lock"} or not path.exists():
                continue
            if pattern.search(path.read_text(errors="ignore")):
                hits.append(path.relative_to(ROOT).as_posix())
        assert hits == []

    def test_the_docs_and_readme_show_only_the_placeholder(self):
        for path in (ROOT / "README.md", *ROOT.joinpath("docs").glob("*.md")):
            for line in path.read_text().splitlines():
                if "UPCITEMDB_API_KEY" in line and "=" in line:
                    assert "your_api_key_here" in line or line.strip().startswith("|"), (path.name, line)


class TestSecretsStayInTheBackend:
    def test_the_key_is_a_secret_setting_read_from_the_environment_only(self):
        source = (ROOT / "backend/app/core/config.py").read_text()
        assert "SecretStr" in source and "UPCITEMDB_API_KEY" in source
        assert "your_api_key_here" not in source.replace(
            '"your_api_key_here"', ""
        )  # only the placeholder check

    def test_the_key_is_never_written_to_the_database(self):
        from app.models import Base

        columns = {c.name.lower() for table in Base.metadata.tables.values() for c in table.columns}
        assert not {c for c in columns if "api_key" in c or "secret" in c or c in {"user_key", "token"}}

    def test_no_route_returns_settings_or_the_key(self, client_a):
        for path in (
            "/api/v1/price-intelligence/providers",
            "/api/v1/subscription",
            "/api/v1/shop",
            "/health",
        ):
            response = client_a.get(path)
            assert "upcitemdb_api_key" not in response.text.lower() and "user_key" not in response.text
