"""Rules from docs/ARCHITECTURE.md, enforced by reading the source code.

If one of these fails, either the code broke a boundary or the boundary was changed on purpose. In the
second case update the docs first, then this test.
"""

import re
from pathlib import Path

import pytest

from app.core.config import BACKEND_DIR

APP_DIR = BACKEND_DIR / "app"


def source_files() -> list[Path]:
    return sorted(APP_DIR.rglob("*.py"))


def relative(path: Path) -> str:
    return path.relative_to(APP_DIR).as_posix()


def imports_of(path: Path, package: str) -> list[str]:
    pattern = re.compile(rf"^\s*(?:from|import)\s+{re.escape(package)}\b.*$", re.MULTILINE)
    return pattern.findall(path.read_text())


# --- Single writer per ledger ---------------------------------------------------------------------

# name -> path prefixes that may mention it. Models define the table; the service is the only writer;
# the (future, read-only) reporting package may read it.
LEDGER_ACCESS = {
    r"\bInventoryTransaction\b": ("models/", "services/inventory_service.py", "reporting/"),
    r"\binventory_transactions\b": ("models/", "services/inventory_service.py", "reporting/"),
    r"\bCustomerLedgerEntry\b": ("models/", "services/khata_service.py", "reporting/"),
    r"\bcustomer_ledger\b": ("models/", "services/khata_service.py", "reporting/"),
}


@pytest.mark.parametrize("pattern", LEDGER_ACCESS)
def test_only_the_owning_service_touches_each_ledger(pattern: str):
    allowed = LEDGER_ACCESS[pattern]
    offenders = [
        relative(path)
        for path in source_files()
        if re.search(pattern, path.read_text()) and not relative(path).startswith(allowed)
    ]

    assert offenders == [], f"{pattern} may only be used under {allowed}"


def test_the_ledger_writer_services_exist():
    assert (APP_DIR / "services" / "inventory_service.py").is_file()
    assert (APP_DIR / "services" / "khata_service.py").is_file()


# --- Layering: lower layers never import upper ones ------------------------------------------------

LAYER_RULES = [
    ("db", "app.models"),
    ("db", "app.services"),
    ("db", "app.api"),
    ("models", "app.services"),
    ("models", "app.api"),
    ("services", "app.api"),
]


@pytest.mark.parametrize(("layer", "forbidden"), LAYER_RULES)
def test_layers_do_not_import_upwards(layer: str, forbidden: str):
    offenders = [
        f"{relative(path)}: {line.strip()}"
        for path in (APP_DIR / layer).rglob("*.py")
        for line in imports_of(path, forbidden)
    ]

    assert offenders == [], f"app/{layer} must not import {forbidden}"


def test_alembic_migrations_never_import_application_code():
    for path in (BACKEND_DIR / "migrations" / "versions").glob("*.py"):
        assert imports_of(path, "app") == [], path.name
