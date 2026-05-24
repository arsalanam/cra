"""Pydantic schemas — the typed contracts between agent specialists and the frontend.

Schemas live here, not in `agent/`, so they can be referenced by:
  • multiple specialists (general_qa, meta_analysis, …)
  • persistence (Thread.workflow stores the discriminator field)
  • frontend (via FastAPI's response_model)
without creating a circular dependency on agent code.

Sub-modules:
  common         — shared shapes (ClarificationRequest, Answer)
  meta_analysis  — meta-analysis workflow shapes (PicoTable, StudyCandidate, …)
"""
