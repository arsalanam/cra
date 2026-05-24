"""Unit tests for the sandboxed Python REPL."""

from research_assistant.tools.data_science.python_repl import _impl


async def test_basic_print() -> None:
    result = await _impl("print('hello')")
    assert "hello" in result


async def test_no_output() -> None:
    result = await _impl("x = 42")
    assert "no output" in result.lower()


async def test_multiline_code() -> None:
    code = "for i in range(3):\n    print(i)"
    result = await _impl(code)
    assert "0" in result
    assert "1" in result
    assert "2" in result


async def test_math_import_allowed() -> None:
    result = await _impl("import math\nprint(math.factorial(5))")
    assert "120" in result


async def test_json_pre_imported() -> None:
    result = await _impl("print(json.dumps({'a': 1}))")
    assert '"a"' in result


async def test_allowed_import_re() -> None:
    result = await _impl("import re\nprint(re.findall(r'\\d+', 'abc123def456'))")
    assert "123" in result


async def test_denied_import() -> None:
    result = await _impl("import os")
    assert "not allowed" in result.lower() or "error" in result.lower()


async def test_denied_import_subprocess() -> None:
    result = await _impl("import subprocess")
    assert "not allowed" in result.lower() or "error" in result.lower()


async def test_open_denied() -> None:
    result = await _impl("open('/etc/passwd')")
    assert "error" in result.lower()


async def test_eval_denied() -> None:
    result = await _impl("eval('1+1')")
    assert "error" in result.lower()


async def test_exec_denied() -> None:
    result = await _impl("exec('print(1)')")
    assert "error" in result.lower()


async def test_syntax_error() -> None:
    result = await _impl("def foo(")
    assert "error" in result.lower()


async def test_runtime_error() -> None:
    result = await _impl("print(1/0)")
    assert "error" in result.lower()
