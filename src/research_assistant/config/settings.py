"""Bootstrap configuration loaded from environment / .env.

Use this for things that must be present at import time and don't change
between requests (database URL, AWS region, base model ID, default API
keys). Runtime-editable settings (per-source API keys, rate limits, on/off
flags configured by an admin via UI) belong in a future `config.service`
module that overlays the DB-backed `SourceConfig` table on top of these
defaults.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Haiku is the cost-conscious default. Switch to a Sonnet / Opus tier
    # via BEDROCK_MODEL_ID env when a workflow needs deeper reasoning
    # (e.g. complex meta-analysis with many included studies). Per-workflow
    # model selection is on the roadmap; for now it's a single env knob.
    # Note: Bedrock requires the full inference-profile ID for Haiku 4.5 —
    # the short-form `us.anthropic.claude-haiku-4-5` is rejected; the
    # short-form alias only exists for Sonnet 4.6.
    bedrock_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    aws_region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices("aws_region", "AWS_DEFAULT_REGION", "AWS_REGION"),
    )
    app_port: int = 8000
    log_level: str = "info"

    # Agent safety/usage limits. These cap what a single /api/turn run can do,
    # so a runaway loop can't burn tokens or hammer tools indefinitely.
    max_model_requests: int = 100  # pydantic-ai default; raise for deeper reasoning
    # Per-workflow tool-call caps live next to each specialist's prompt
    # (search for `_MAX_TOOL_CALLS` in agent/specialists/*.py). A single
    # global cap proved too coarse — exploration-heavy general_qa needs
    # 80, workflow-gated meta_analysis is fine at 40.
    # Wall-clock cap per turn. Must align with the per-workflow tool-call
    # envelopes: the largest cap is meta_analysis at 100 calls; general_qa
    # sits at 40 (it was 80 only while routing errors dragged Q&A turns
    # into tool-heavy spirals). 300s keeps the UX synchronous. Raise for
    # unusually deep questions; lower if the UI needs sharper
    # responsiveness.
    agent_timeout_seconds: float = 300.0

    # Daily token budgets (UTC). Aggregated across all users / threads /
    # background jobs from persisted `done` stream events. The dispatcher
    # endpoint and watch runner pre-flight against these and refuse new
    # work once exceeded — turns return HTTP 429, watch runs are recorded
    # as `quota_exceeded` and skipped. Defaults sit comfortably under a
    # typical Bedrock per-account daily quota so the app stops short of
    # upstream throttling. Set to 0 to disable enforcement.
    max_input_tokens_per_day: int = 3_000_000
    max_output_tokens_per_day: int = 600_000

    # Dispatcher LLM fallback classifier (step 9 in docs/agent-loop-review.md).
    # When the regex cascade finds NO keyword match on a first turn (rule
    # "default"), one cheap structured Bedrock call picks the workflow instead
    # of silently landing in general_qa. Fail-open: any error / timeout keeps
    # the general_qa default, so the dispatcher is never worse than the regex
    # behaviour. Disabled in tests via conftest env.
    dispatcher_llm_fallback_enabled: bool = True
    dispatcher_llm_fallback_timeout_seconds: float = 10.0

    # Vision model for image description (Sonnet recommended)
    vision_model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0"

    # Tavily web search
    tavily_api_key: str = ""

    # NCBI E-utilities. With a key NCBI raises the rate limit from 3 req/s to
    # 10 req/s; chained mesh_lookup → pubmed_search → fetch_pmc_fulltext calls
    # hit the unauthenticated cap easily.
    # Get one for free: https://account.ncbi.nlm.nih.gov/settings/
    ncbi_api_key: str = ""

    # Docker sandbox for data science / meta-analysis
    sandbox_image: str = "research-assistant-sandbox:latest"
    sandbox_timeout_seconds: int = 60
    sandbox_memory_limit: str = "512m"
    sandbox_cpu_count: float = 1.0
    sandbox_enabled: bool = True

    # Filesystem directory where generated image artefacts (forest plots,
    # funnel plots, …) are written. Served at /images by the FastAPI app.
    # In k8s, override to the shared-FS mount point (e.g. "/mnt/shared/images")
    # so multiple agent replicas and future containers (eCRF, data collector)
    # see the same artefacts.
    images_dir: str = "images"

    # Local publication cache (R0). Raw full-text/PDF blobs are written under
    # library_raw_dir, content-addressable by SHA-256; abstracts + metadata
    # live in Postgres. In k8s, point this at the shared document-cache volume
    # so every agent replica sees the same blobs. library_cache_enabled gates
    # the write-through hooks in search_papers / fetch_pmc_fulltext — turn it
    # off to disable caching without touching the tools.
    library_raw_dir: str = "library"
    library_cache_enabled: bool = True

    # Embeddings (R2). Titan Text Embeddings v2 on Bedrock — access verified
    # on the account (no use-case gate, unlike Anthropic models). The pgvector
    # column is fixed at embedding_dimensions; CHANGING IT REQUIRES A RE-EMBED
    # (see scripts/backfill_embeddings.py --reembed). embedding_max_concurrency
    # bounds parallel Bedrock calls (Titan has no batch API). embedding_enabled
    # gates the background drain + write-through nudges. The model+dim are
    # stamped onto each passage as `<model_id>@<dims>` so mixed-model states
    # and re-embeds are detectable.
    embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dimensions: int = 1024
    embedding_max_concurrency: int = 4
    embedding_enabled: bool = True

    # Persistence. Defaults to Postgres via the compose-network hostname;
    # tests override via env to in-memory SQLite for speed. Override
    # DATABASE_URL when running against a different host (e.g. an external
    # managed Postgres).
    database_url: str = "postgresql+asyncpg://cra:cra@postgres:5432/cra"

    # Clinical-data store (eCRF E1+). A SEPARATE Postgres for subject PHI
    # (decision D2 — isolation: own credentials/backup/residency), distinct
    # from the research-app `database_url` above. The agent holds both
    # connections; the Data Collector (E4) reaches this store only via the
    # agent API. Tests override to a second in-memory SQLite.
    clinical_database_url: str = "postgresql+asyncpg://cra:cra@clinical-postgres:5432/cra_clinical"

    # AWS Cognito (identity). Populated by scripts/cognito_setup.py; all
    # blank in dev/test environments — the auth middleware short-circuits
    # when cognito_user_pool_id is empty so non-auth code paths keep
    # working in pytest. Real deployments MUST set these.
    cognito_region: str = ""
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_client_secret: str = ""
    cognito_domain: str = ""  # e.g. https://cra-arsalanam.auth.us-east-1.amazoncognito.com
    cognito_redirect_uri: str = "http://localhost:8000/auth/callback"

    # ── ALB edge auth (Option A) ─────────────────────────────────────────
    # When the load balancer runs the Cognito login (not the app), the app
    # exposes GET /logout to expire the ALB session cookies AND end the
    # Cognito hosted-UI session. Independent of `auth_enabled` — the app's
    # own login stays off. Populated by the EC2 deploy when enable_alb_auth.
    alb_auth_enabled: bool = False
    alb_cognito_domain: str = ""
    alb_cognito_client_id: str = ""
    alb_post_logout_url: str = ""

    # Session cookie signing key. Must be a high-entropy random string;
    # tests get a fixed value via env. Generate one with:
    #   python -c "import secrets; print(secrets.token_urlsafe(64))"
    session_cookie_secret: str = ""

    @property
    def auth_enabled(self) -> bool:
        """Auth is enforced only when Cognito is fully configured.

        Lets pytest + early dev keep running without auth wiring until the
        operator has populated the Cognito settings.
        """
        return bool(self.cognito_user_pool_id and self.cognito_client_id)

    context_window_messages: int = 20
    summarize_after_messages: int = 500


def get_settings() -> Settings:
    """Factory — instantiate Settings (kept as a function for testability)."""
    return Settings()
