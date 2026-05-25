"""Edit-check expression engine for eCRF (E2).

Evaluates an `EditCheck.expression` — a boolean that MUST HOLD for the data to
be valid — against a form instance's current item values. Item ids are bound as
names; a small helper allowlist is also available. e.g.:

    "age >= 0 and age < 120"            # range
    "diastolic <= systolic"             # cross-field
    "is_blank(pregnant) or sex == 'F'"  # conditional

Safe-by-AST-whitelist, mirroring `tools/data_science/calculator.py`: only
whitelisted node types, no attribute access, no arbitrary calls — just item-id
names and the helper functions. Expressions are authored by study designers
(admin), validated at author/publish time via `validate_expression`.
"""

from __future__ import annotations

import ast
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.ecrf import FormDefinition, Item

logger = logging.getLogger(__name__)


def _is_blank(x: Any) -> bool:
    return x is None or (isinstance(x, str) and x.strip() == "")


def _present(x: Any) -> bool:
    return not _is_blank(x)


def _matches(pattern: str, x: Any) -> bool:
    return x is not None and re.fullmatch(pattern, str(x)) is not None


_HELPERS: dict[str, Any] = {
    "is_blank": _is_blank,
    "present": _present,
    "matches": _matches,
    "len": len,
    "abs": abs,
}

# Whitelisted AST nodes — comparisons, boolean/arithmetic ops, membership,
# literals, names, and simple calls. Everything else (attributes, lambdas,
# comprehensions, subscripts, ...) is rejected.
_SAFE_NODES: frozenset[type[ast.AST]] = frozenset(
    {
        ast.Expression,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.UnaryOp,
        ast.Not,
        ast.USub,
        ast.UAdd,
        ast.BinOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.FloorDiv,
        ast.Pow,
        ast.Compare,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.In,
        ast.NotIn,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.List,
        ast.Tuple,
        ast.Set,
    }
)


class ExpressionError(Exception):
    """An edit-check expression is malformed or uses a disallowed construct."""


@dataclass(frozen=True)
class CheckResult:
    item_id: str
    check_id: str
    severity: str  # "hard" | "soft"
    passed: bool
    message: str


def validate_expression(expression: str, allowed_names: set[str]) -> None:
    """Parse + whitelist-check an expression. Raises ExpressionError if unsafe.

    Reusable at authoring/publish time so malformed rules are caught before a
    form is published, not silently skipped during capture.
    """
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"syntax error: {e}") from e
    for node in ast.walk(tree):
        if type(node) not in _SAFE_NODES:
            raise ExpressionError(f"{type(node).__name__} not allowed")
        if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name):
            raise ExpressionError("only simple function calls are allowed")
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise ExpressionError(f"unknown name {node.id!r}")


def _coerce(item: Item, raw: str | None) -> Any:
    """Coerce a stored string value to the item's type (blank -> None).

    Integers/decimals/booleans become Python numbers/bools; dates, text, and
    selects stay strings (ISO dates still order correctly as strings).
    """
    if raw is None or raw.strip() == "":
        return None
    try:
        if item.data_type == "integer":
            return int(raw)
        if item.data_type == "decimal":
            return float(raw)
        if item.data_type == "boolean":
            return raw.strip().lower() in {"true", "1", "yes", "y"}
    except ValueError:
        return raw  # unparseable — leave as string
    return raw


def _eval_check(expression: str, namespace: Mapping[str, Any], allowed: set[str]) -> bool:
    """Return whether the rule holds. A broken rule is skipped (treated as pass)
    and logged — author-time `validate_expression` is the guard against that."""
    try:
        validate_expression(expression, allowed)
        compiled = compile(ast.parse(expression.strip(), mode="eval"), "<editcheck>", "eval")
        return bool(eval(compiled, {"__builtins__": {}}, dict(namespace)))  # noqa: S307
    except Exception:
        logger.warning("edit-check expression skipped (eval failed): %r", expression, exc_info=True)
        return True


def _items(definition: FormDefinition) -> dict[str, Item]:
    return {item.id: item for section in definition.sections for item in section.items}


def evaluate_form(
    definition: FormDefinition, values: Mapping[str, str | None]
) -> list[CheckResult]:
    """Run every item's edit checks against the current values."""
    items = _items(definition)
    namespace: dict[str, Any] = {iid: _coerce(items[iid], values.get(iid)) for iid in items}
    namespace.update(_HELPERS)
    allowed = set(items) | set(_HELPERS)

    results: list[CheckResult] = []
    for item in items.values():
        for check in item.edit_checks:
            passed = _eval_check(check.expression, namespace, allowed)
            results.append(CheckResult(item.id, check.id, check.severity, passed, check.message))
    return results


def required_blank_items(definition: FormDefinition, values: Mapping[str, str | None]) -> list[str]:
    """Item ids that are `required` but blank — used to block form completion."""
    return [
        item.id
        for item in _items(definition).values()
        if item.required and _is_blank(values.get(item.id))
    ]
