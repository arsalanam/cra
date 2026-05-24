-- Postgres bootstrap for the CRA database.
--
-- Runs once on a fresh `postgres` volume (docker-entrypoint-initdb.d
-- semantics). Idempotent — re-runnable if the volume is recreated.
--
-- Schema creation (CREATE TABLE) is owned by the agent's init_db at
-- startup; this file only handles things SQLAlchemy can't drive:
--   • pgvector extension — enabled now so RAG work can land later
--     without a separate migration.

CREATE EXTENSION IF NOT EXISTS vector;
