"""CDISC submission pipeline (top-6 #6).

SDTM domain mapping (DM / AE / VS) + ADaM derivation (ADSL) + TLF
generation (subject disposition + demographics + AE summary tables +
AE-frequency bar chart figure) + submission-bundle CSV/ZIP export.

Internal Python implementation; no R dependency. The `CdiscMapper`
Protocol in `sdtm_mapper.py` is the seam where a future deploy can plug
in pinnacle / OAK via subprocess if the regulatory team prefers an
OSS-recognised tool. Controlled terminology ships as JSON in
`terminology/`.
"""

from __future__ import annotations
