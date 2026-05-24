"""Integration tests for the library upload + browse endpoints (R1/R4)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from research_assistant.persistence.database import reset_engine


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("LIBRARY_RAW_DIR", str(tmp_path / "lib"))
    reset_engine()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from research_assistant.persistence.database import init_db
    from research_assistant.web.app import create_app

    app = create_app()
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    reset_engine()


def _make_pdf(body: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    if body:
        page.insert_textbox(fitz.Rect(50, 50, 550, 780), body, fontsize=11)
    data: bytes = doc.tobytes()
    doc.close()
    return data


_LONG_TEXT = (
    "Introduction\n\n"
    "Empagliflozin reduces heart failure hospitalization in patients with "
    "type 2 diabetes. "
    * 12
    + "\n\nMethods\n\nA randomized double-blind placebo-controlled trial was conducted. " * 4
)


async def test_upload_ingests_and_dedupes(client: AsyncClient) -> None:
    pdf = _make_pdf(_LONG_TEXT)

    resp = await client.post(
        "/api/library/upload",
        files={"file": ("paper.pdf", pdf, "application/pdf")},
        data={"title": "Empagliflozin RCT"},
    )
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["title"] == "Empagliflozin RCT"
    assert out["passages_created"] >= 1
    assert out["already_cached"] is False
    pub_id = out["publication_id"]

    # Stats reflect the upload.
    stats = (await client.get("/api/library/stats")).json()
    assert stats["publications"] == 1
    assert stats["fulltext_publications"] == 1
    assert stats["by_source"].get("upload") == 1

    # Listing shows it with full text.
    listing = (await client.get("/api/library")).json()
    assert any(p["id"] == pub_id and p["has_fulltext"] for p in listing)

    # Detail returns passages.
    detail = (await client.get(f"/api/library/{pub_id}")).json()
    assert detail["passage_count"] >= 1
    assert len(detail["passages"]) == detail["passage_count"]
    assert detail["passages"][0]["section"] in {"introduction", "methods", "body"}

    # Re-uploading the identical file dedupes onto the same publication.
    again = await client.post(
        "/api/library/upload",
        files={"file": ("paper.pdf", pdf, "application/pdf")},
        data={"title": "Empagliflozin RCT"},
    )
    assert again.status_code == 201
    assert again.json()["already_cached"] is True
    assert again.json()["publication_id"] == pub_id
    assert (await client.get("/api/library/stats")).json()["publications"] == 1


async def test_delete_publication(client: AsyncClient) -> None:
    pdf = _make_pdf(_LONG_TEXT)
    pub_id = (
        await client.post(
            "/api/library/upload",
            files={"file": ("p.pdf", pdf, "application/pdf")},
        )
    ).json()["publication_id"]

    assert (await client.delete(f"/api/library/{pub_id}")).status_code == 204
    assert (await client.get("/api/library/stats")).json()["publications"] == 0
    assert (await client.delete(f"/api/library/{pub_id}")).status_code == 404


async def test_rejects_non_pdf(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/library/upload",
        files={"file": ("notes.txt", b"just some text", "text/plain")},
    )
    assert resp.status_code == 422
    assert "PDF" in resp.json()["detail"]


async def test_rejects_scanned_pdf_without_text(client: AsyncClient) -> None:
    blank = _make_pdf("")  # valid PDF, no extractable text
    resp = await client.post(
        "/api/library/upload",
        files={"file": ("scan.pdf", blank, "application/pdf")},
    )
    assert resp.status_code == 422
    assert "scanned" in resp.json()["detail"].lower()
