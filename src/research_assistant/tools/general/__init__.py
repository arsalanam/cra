"""General-purpose tools — universally available to all specialists.

These don't depend on any specialised data source: web search, Wikipedia
lookup, document/file readers, image description.
"""

from . import (
    describe_image,
    fetch_document,
    import_citations,
    read_file,
    web_search,
    wikipedia,
)

__all__ = [
    "describe_image",
    "fetch_document",
    "import_citations",
    "read_file",
    "web_search",
    "wikipedia",
]
