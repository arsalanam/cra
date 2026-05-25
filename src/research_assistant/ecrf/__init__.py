"""eCRF subsystem (E0+).

E0 surface: form-definition authoring/registry lives in `domain.ecrf` +
`persistence.ecrf_repository` + the `/api/ecrf` router; this package holds the
standards-export helpers (CDISC ODM-XML today, Define-XML later).
"""

from __future__ import annotations

from .odm_export import form_to_odm_xml

__all__ = ["form_to_odm_xml"]
