"""Entry point: `python -m research_assistant` or `research-assistant`."""

from __future__ import annotations

import logging

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()

    # Configure the package-level logger so all research_assistant.* loggers
    # inherit the same level and format. Uvicorn configures its own loggers
    # separately via the log_level param below.
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    uvicorn.run(
        "research_assistant.web.app:app",
        host="0.0.0.0",
        port=settings.app_port,
        reload=True,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
