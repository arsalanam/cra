"""Lab-data ingest parsers (P2 #6).

Three source formats are supported, each producing a stream of the
same shape (`ParsedLabResult`) so the repository's `ingest_lab_results`
method handles them uniformly:

  • HL7 v2 ORU^R01 — pipe-delimited segments (`hl7v2.parse`)
  • CDISC LAB tab-delimited (`cdisc_lab.parse`)
  • HL7 FHIR R4 Bundle of DiagnosticReport + Observation (`fhir.parse`)

All three parsers are stdlib-only — no hl7apy / fhir.resources / etc.
dependency. The wire formats are stable and the platform doesn't need
the validation surface those libraries provide; it needs the parsed
fields the SDTM LB derivation cares about.
"""

from __future__ import annotations

from .common import ParsedLabBatch, ParsedLabResult, parse_lab_payload

__all__ = ["ParsedLabBatch", "ParsedLabResult", "parse_lab_payload"]
