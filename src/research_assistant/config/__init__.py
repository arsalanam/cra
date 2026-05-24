"""Configuration layer.

Two parts:
  • `settings`     — bootstrap config (.env / environment variables) loaded
                    once via pydantic-settings. Database URL, AWS region,
                    base model ID, etc. — things that change per deployment,
                    not at runtime.
  • `rate_limit`   — generic HTTP client with retry/backoff, configurable per
                    paper source.

A future `service.py` will add a DB-backed runtime config layer (admin-
editable source API keys, rate limits, enabled flags) that overlays
settings without changing the consumer surface.
"""

from .settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
