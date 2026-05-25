"""Clinical-data (PHI) store for the eCRF subsystem (E1+).

A datastore physically separate from the research-app database (decision
D2 in `ecrf-design.md`): subject data, captured values, and the audit trail
live here with their own engine/credentials. The agent is the only process
that connects to it; the Data Collector container (E4) reaches it via the
agent API (D5). No PHI ever flows to Bedrock or the RAG index (D6).
"""
