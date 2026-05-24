"""Unit tests for the calculator tool."""

from research_assistant.tools.data_science.calculator import _safe_calculate


def test_basic_arithmetic() -> None:
    assert _safe_calculate("2 + 3") == "5"
    assert _safe_calculate("10 - 4") == "6"
    assert _safe_calculate("6 * 7") == "42"


def test_division() -> None:
    assert _safe_calculate("10 / 4") == "2.5"
    assert _safe_calculate("10 // 3") == "3"


def test_modulo() -> None:
    assert _safe_calculate("17 % 5") == "2"


def test_power() -> None:
    assert _safe_calculate("2 ** 10") == "1024"


def test_math_functions() -> None:
    assert _safe_calculate("sqrt(144)") == "12"
    assert _safe_calculate("abs(-5)") == "5"
    assert _safe_calculate("ceil(4.2)") == "5"
    assert _safe_calculate("floor(4.9)") == "4"


def test_constants() -> None:
    result = float(_safe_calculate("pi"))
    assert abs(result - 3.14159265) < 1e-5
    result = float(_safe_calculate("e"))
    assert abs(result - 2.71828182) < 1e-5


def test_nested_expression() -> None:
    assert _safe_calculate("sqrt(pow(3, 2) + pow(4, 2))") == "5"


def test_float_formatting() -> None:
    assert _safe_calculate("1.0 + 0.0") == "1"
    assert "." in _safe_calculate("1 / 3")


def test_division_by_zero() -> None:
    assert "Division by zero" in _safe_calculate("1 / 0")


def test_syntax_error() -> None:
    assert "Syntax error" in _safe_calculate("2 +")


def test_unsafe_name_rejected() -> None:
    result = _safe_calculate("__import__('os')")
    assert "Unsafe" in result or "Unknown" in result


def test_unknown_name_rejected() -> None:
    result = _safe_calculate("foo(1)")
    assert "Unknown name" in result


def test_attribute_access_rejected() -> None:
    result = _safe_calculate("(1).__class__")
    assert "Unsafe" in result
