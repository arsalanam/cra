"""Downloadable reports generated from persisted thread state.

One module per report shape. Each exposes the same trio of names — the
endpoint glue (`web/threads.py`) imports them via a dispatch dict so
adding a fourth report type is a matter of writing the module + adding
one entry to the dispatch dict:

    assemble_report_data(thread_id, messages) -> dataclass | None
    build_pdf(data, images_dir)               -> bytes
    build_docx(data, images_dir)              -> bytes

Shared PDF + DOCX palette and helpers live in `_shared_styles.py`.
"""
