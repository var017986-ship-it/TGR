"""Contract checks against the supplied FunPay Universal 1.18.2 API."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import FunPayAPI
from FunPayAPI import Account, Runner
from FunPayAPI.common import enums
from FunPayAPI.updater import events


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_ROOT = (
    Path(r"C:\Users\var01\Downloads\доп штуки по фанпей")
    / "funpay-universal-1.18.2"
    / "funpay-universal-1.18.2"
)
PUBLIC_FILES = (
    "account.py",
    "types.py",
    "common/enums.py",
    "updater/runner.py",
    "updater/events.py",
)


def _top_level_methods(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name: ast.unparse(node.args)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.col_offset == 4
    }


def test_funpay_api_public_method_contract_matches_reference() -> None:
    for relative_path in PUBLIC_FILES:
        project = _top_level_methods(PROJECT_ROOT / "FunPayAPI" / relative_path)
        reference = _top_level_methods(REFERENCE_ROOT / "FunPayAPI" / relative_path)
        assert project.keys() == reference.keys(), relative_path
        assert project == reference, relative_path


def test_funpay_api_runtime_exports_are_available() -> None:
    assert inspect.isclass(Account)
    assert inspect.isclass(Runner)
    assert hasattr(FunPayAPI, "types")
    assert hasattr(enums, "OrderStatuses")
    assert hasattr(enums, "MessageTypes")
    assert hasattr(events, "NewOrderEvent")
    assert hasattr(events, "NewMessageEvent")
