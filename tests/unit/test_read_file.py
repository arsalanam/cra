"""Unit tests for the file-reader tool."""

from research_assistant.tools.general.read_file import _parse


def test_json_by_extension(sample_json: str) -> None:
    result = _parse(sample_json, "data.json")
    assert "JSON file parsed" in result
    assert "hello" in result


def test_json_by_content() -> None:
    result = _parse('{"key": "value"}', "unknown.txt")
    assert "JSON file parsed" in result


def test_csv_by_extension(sample_csv: str) -> None:
    result = _parse(sample_csv, "data.csv")
    assert "CSV file" in result
    assert "2 data rows" in result


def test_csv_by_content() -> None:
    content = "a,b,c\n1,2,3\n4,5,6\n"
    result = _parse(content, "file.dat")
    assert "CSV file" in result


def test_plain_text() -> None:
    text = "This is a plain text document with no commas in the first two hundred characters of content here"
    result = _parse(text, "notes.txt")
    assert "Text file" in result
    assert "words" in result


def test_large_text_truncated() -> None:
    text = "word " * 500
    result = _parse(text, "big.txt")
    assert "..." in result


def test_empty_file() -> None:
    result = _parse("", "empty.txt")
    assert "Text file" in result
