"""Unit tests for describe_image tool (format detection logic)."""

from research_assistant.tools.general.describe_image import _guess_format


def test_guess_jpeg_from_content_type() -> None:
    assert _guess_format("https://example.com/img", "image/jpeg") == "jpeg"


def test_guess_png_from_content_type() -> None:
    assert _guess_format("https://example.com/img", "image/png") == "png"


def test_guess_gif_from_content_type() -> None:
    assert _guess_format("https://example.com/img", "image/gif") == "gif"


def test_guess_webp_from_content_type() -> None:
    assert _guess_format("https://example.com/img", "image/webp") == "webp"


def test_guess_jpeg_from_jpg_extension() -> None:
    assert _guess_format("https://example.com/photo.jpg", "") == "jpeg"


def test_guess_png_from_extension() -> None:
    assert _guess_format("https://example.com/chart.png", "") == "png"


def test_guess_ignores_query_params() -> None:
    assert _guess_format("https://example.com/photo.png?w=800", "") == "png"


def test_guess_unknown_returns_none() -> None:
    assert _guess_format("https://example.com/file.bmp", "application/octet-stream") is None


def test_guess_content_type_with_charset() -> None:
    assert _guess_format("https://example.com/img", "image/jpeg; charset=utf-8") == "jpeg"
