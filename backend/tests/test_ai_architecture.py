"""The AI layer has no raw write access: it reads through fixed tools and writes only through a confirmed action.

Checked on the syntax tree, not the text, so a docstring that says "post" or a plain `set.add` is not a false alarm.
"""

import ast
import re
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"
READ_ONLY = [
    "ai_tools.py",
    "ai_planner.py",
    "ai_insights_service.py",
    "analytics_service.py",
    "ai_answer.py",
    "ai_dates.py",
    "ai_format.py",
    "ai_provider.py",
]
AI_MODULES = sorted(
    {p.name for p in SERVICES.glob("ai_*.py")} | {"document_intelligence_service.py", "analytics_service.py"}
)
SESSION_WRITES = {
    "add",
    "add_all",
    "delete",
    "commit",
    "merge",
    "flush",
    "rollback",
    "bulk_save_objects",
    "bulk_insert_mappings",
}
SQL_WRITERS = {"insert", "update", "delete", "text"}
WRITE_DOORS = {"write_transaction", "record_audit"}
WRITING_SERVICE_CALLS = re.compile(
    r"^(create_purchase|post_purchase|record_adjustment|create_promotion|activate\w*|post_sale|record_payment|void_\w+|update_product|set_\w*price\w*)$"
)


def tree(name: str) -> ast.AST:
    return ast.parse((SERVICES / name).read_text())


def identifiers(node: ast.AST):
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            yield n.id
        elif isinstance(n, ast.Attribute):
            yield n.attr
        elif isinstance(n, ast.alias):
            yield n.name.split(".")[-1]


def sqlalchemy_imports(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("sqlalchemy"):
            names |= {a.name for a in n.names}
    return names


def session_write_calls(node: ast.AST):
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in SESSION_WRITES:
            if "session" in ast.unparse(n.func.value):
                yield ast.unparse(n)


@pytest.mark.parametrize("name", READ_ONLY)
def test_the_reading_modules_contain_no_write_operation(name):
    t = tree(name)
    assert list(session_write_calls(t)) == []
    assert not (set(identifiers(t)) & WRITE_DOORS)
    assert not (sqlalchemy_imports(t) & SQL_WRITERS), f"{name} imports a SQL writer or raw text()"


@pytest.mark.parametrize("name", AI_MODULES)
def test_no_ai_module_runs_raw_sql(name):
    t = tree(name)
    assert not (sqlalchemy_imports(t) & {"text"}), f"{name} imports raw text()"
    assert not ({"exec_driver_sql", "sqlite3"} & set(identifiers(t))), f"{name} runs raw SQL"
    for n in ast.walk(t):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "execute"
            and n.args
        ):
            assert not isinstance(n.args[0], ast.Constant), f"{name} executes a string"


def test_only_the_action_service_reaches_the_services_that_change_business_data():
    """Confirming an action is the single door to purchase, stock-adjustment and offer creation."""
    for name in AI_MODULES:
        if name == "ai_action_service.py":
            continue
        for n in ast.walk(tree(name)):
            if isinstance(n, ast.Call):
                callee = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                assert not WRITING_SERVICE_CALLS.match(callee), f"{name} calls {callee}"


def test_the_action_service_can_only_do_what_the_documented_list_says():
    calls = set()
    for n in ast.walk(tree("ai_action_service.py")):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
        ):
            if n.func.value.id.endswith("_service"):
                calls.add((n.func.value.id, n.func.attr))
    allowed = {
        ("purchase_service", "create_purchase"), ("purchase_service", "compute_line_total"),
        ("inventory_service", "record_adjustment"), ("inventory_service", "get_stock"),
        ("inventory_service", "get_stock_map"), ("inventory_service", "lock_products"),
        ("promotion_service", "create_promotion"), ("entitlement_service", "require_feature"),
        ("authorization_service", "require"),  # the action needs the permission of what it does (Phase 12)
        ("task_service", "create"),  # TASK_DRAFT: a task, never anything financial or inventory-changing (Phase 13)
        ("campaign_service", "create"),  # CAMPAIGN_DRAFT: a DRAFT campaign, never launched (Phase 14)
    }  # fmt: skip
    assert calls <= allowed, calls - allowed
    for banned in ("post_purchase", "activate_promotion", "void_purchase", "post_sale", "record_payment"):
        assert banned not in {c[1] for c in calls}


def test_tools_take_no_shop_argument_and_reject_unknown_arguments():
    from app.services.ai_tools import TOOLS

    assert len(TOOLS) >= 20
    for tool in TOOLS.values():
        assert tool.args.model_config.get("extra") == "forbid", tool.name
        assert not {"shop_id", "sql", "query", "user_id"} & set(tool.args.model_fields), tool.name


def test_the_ai_router_has_no_delete_route_and_runs_no_sql():
    t = ast.parse((SERVICES.parent / "api" / "v1" / "ai.py").read_text())
    assert not (sqlalchemy_imports(t) - {"Session"})
    methods = {
        d.func.attr
        for n in ast.walk(t)
        if isinstance(n, ast.FunctionDef)
        for d in n.decorator_list
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
    }
    assert methods <= {"get", "post", "patch"}
