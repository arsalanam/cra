"""SDTM domain derivation — DM, AE, VS (top-6 #6).

`CdiscMapper` is a Protocol so a future deploy can swap in an
OSS-backed implementation (pinnacle / OAK via subprocess) without
touching the calling endpoints. The built-in `BuiltinPythonMapper`
implements DM / AE / VS in pure Python against our SQLAlchemy clinical-
store models.

Item-mapping convention: by default we identify DM and VS source items
by their `item_id` (e.g. `age` → DM.AGE, `sbp` → VS.SYSBP). Deployments
that use different item naming conventions pass an `ItemMappingConfig`
to override the defaults at derivation time.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from ..persistence.clinical.models import (
    AdverseEvent,
    ItemData,
    SdtmAe,
    SdtmDm,
    SdtmVs,
    Subject,
)
from .terminology import load as _load_terminology

# Lazy-loaded controlled terminology tables.
_AE_SEVERITY = _load_terminology("ae_severity.json")["mapping"]
_AE_OUTCOME = _load_terminology("ae_outcome.json")["mapping"]
_AE_RELATIONSHIP = _load_terminology("ae_relationship.json")["mapping"]
_VS_TEST_CODES = _load_terminology("vs_test_codes.json")["mapping"]
_DM_SEX = _load_terminology("dm_sex.json")["mapping"]
_DM_RACE = _load_terminology("dm_race.json")["mapping"]


# ── Item-mapping config ─────────────────────────────────────────────────


@dataclass
class ItemMappingConfig:
    """Per-deployment item-id → SDTM-variable overrides.

    Defaults below assume the form designer used conventional item ids
    (`age`, `sex`, `race`, `ethnic`, `sbp`, `dbp`, `hr`, `weight`,
    `height`, `temp`). For deployments using different conventions, pass
    `dm_item_map={'patient_age': 'AGE', ...}` or `vs_item_map={'bp_sys':
    'sbp', ...}` and the mapper will respect them.
    """

    dm_item_map: dict[str, str] = field(
        default_factory=lambda: {
            "age": "AGE",
            "sex": "SEX",
            "race": "RACE",
            "ethnicity": "ETHNIC",
            "ethnic": "ETHNIC",
            "country": "COUNTRY",
            "arm": "ARM",
        }
    )
    vs_item_map: dict[str, str] = field(
        default_factory=lambda: {
            # Map source item_id → canonical key in vs_test_codes.json.
            "height": "height",
            "weight": "weight",
            "sbp": "sbp",
            "sysbp": "sbp",
            "systolic_bp": "sbp",
            "dbp": "dbp",
            "diabp": "dbp",
            "diastolic_bp": "dbp",
            "hr": "hr",
            "pulse": "pulse",
            "temp": "temp",
            "temperature": "temp",
            "rr": "rr",
            "spo2": "spo2",
        }
    )


# ── Protocol ────────────────────────────────────────────────────────────


class CdiscMapper(Protocol):
    """Pluggable interface — built-in here, can be swapped for OSS later."""

    def derive_dm(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects: Iterable[Subject],
        item_data_by_subject: dict[str, list[ItemData]],
        config: ItemMappingConfig,
    ) -> list[SdtmDm]:
        ...

    def derive_ae(
        self,
        *,
        deployment_id: str,
        study_id: str,
        adverse_events: Iterable[AdverseEvent],
        subjects_by_id: dict[str, Subject] | None = None,
    ) -> list[SdtmAe]:
        ...

    def derive_vs(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects: Iterable[Subject],
        item_data_by_subject: dict[str, list[ItemData]],
        config: ItemMappingConfig,
    ) -> list[SdtmVs]:
        ...


# ── Helpers ─────────────────────────────────────────────────────────────


def _to_iso8601(value: Any) -> str | None:
    """Render a date/datetime/string into ISO 8601 (SDTM --DTC format)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return str(dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"))
    if isinstance(value, str):
        # If it parses, normalise; otherwise pass through verbatim.
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return str(parsed.strftime("%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            return value
    return str(value)


def _usubjid(study_id: str, subject_code: str) -> str:
    """USUBJID = STUDYID-SUBJID per SDTM convention."""
    return f"{study_id}-{subject_code}"


def _try_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _ct_lookup(table: dict[str, Any], key: Any) -> str | None:
    """Lookup in a controlled-terminology table; case-insensitive on str."""
    if key is None:
        return None
    if not isinstance(key, str):
        key = str(key)
    return table.get(key) or table.get(key.lower())


# ── Built-in implementation ─────────────────────────────────────────────


class BuiltinPythonMapper:
    """The default `CdiscMapper` implementation — pure Python.

    Items are NOT persisted here — derivation returns ORM instances and
    the calling repository upserts them. This keeps the mapper trivially
    unit-testable (no DB) and lets the API layer wrap each run in a
    single transaction.
    """

    def derive_dm(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects: Iterable[Subject],
        item_data_by_subject: dict[str, list[ItemData]],
        config: ItemMappingConfig,
    ) -> list[SdtmDm]:
        out: list[SdtmDm] = []
        # Reverse the map: item_id → SDTM-variable
        rev = config.dm_item_map
        for subj in subjects:
            items = item_data_by_subject.get(subj.id, [])
            values: dict[str, str] = {}
            for it in items:
                target = rev.get(it.item_id) or rev.get(it.item_id.lower())
                if target and it.value:
                    values[target] = it.value

            sex_raw = values.get("SEX")
            race_raw = values.get("RACE")
            dm = SdtmDm(
                deployment_id=deployment_id,
                STUDYID=study_id,
                DOMAIN="DM",
                USUBJID=_usubjid(study_id, subj.subject_code),
                SUBJID=subj.subject_code,
                SITEID=subj.site_id,
                AGE=_try_int(values.get("AGE")),
                AGEU="YEARS",
                SEX=_ct_lookup(_DM_SEX, sex_raw) or sex_raw,
                RACE=_ct_lookup(_DM_RACE, race_raw) or race_raw,
                ETHNIC=values.get("ETHNIC"),
                ARM=values.get("ARM"),
                COUNTRY=values.get("COUNTRY"),
                RFSTDTC=_to_iso8601(subj.created_at),
            )
            out.append(dm)
        return out

    def derive_ae(
        self,
        *,
        deployment_id: str,
        study_id: str,
        adverse_events: Iterable[AdverseEvent],
        subjects_by_id: dict[str, Subject] | None = None,
    ) -> list[SdtmAe]:
        """`subjects_by_id` maps clinical-store subject id → Subject row
        so we can look up subject_code without relying on a relationship
        the AdverseEvent model doesn't declare. Tests can pass a
        SimpleNamespace via the same dict."""
        out: list[SdtmAe] = []
        # Sort by subject_id then reported_at so AESEQ is stable across
        # re-runs. We compute per-subject sequence numbers below.
        events = sorted(
            adverse_events,
            key=lambda a: (a.subject_id, a.reported_at or datetime.min),
        )
        per_subject_seq: dict[str, int] = {}
        subjects_by_id = subjects_by_id or {}
        for ae in events:
            # Lookup priority: explicit dict (pipeline path), then a
            # `subject` attribute on the AE (test SimpleNamespace path),
            # then the raw subject_id (degraded — gives a non-canonical
            # USUBJID but at least doesn't crash).
            subject = subjects_by_id.get(ae.subject_id) or getattr(ae, "subject", None)
            subj_code = subject.subject_code if subject is not None else ae.subject_id
            usubjid = _usubjid(study_id, subj_code)
            per_subject_seq[ae.subject_id] = per_subject_seq.get(ae.subject_id, 0) + 1
            sev = _ct_lookup(_AE_SEVERITY, str(ae.severity_grade))
            rel = _ct_lookup(_AE_RELATIONSHIP, ae.relationship_to_intervention)
            outcome = _ct_lookup(_AE_OUTCOME, ae.outcome)
            row = SdtmAe(
                deployment_id=deployment_id,
                STUDYID=study_id,
                DOMAIN="AE",
                USUBJID=usubjid,
                AESEQ=per_subject_seq[ae.subject_id],
                AETERM=ae.term_text,
                AEDECOD=ae.meddra_pt,
                AESTDTC=_to_iso8601(ae.start_date),
                AEENDTC=_to_iso8601(ae.end_date),
                AESEV=sev,
                AESER="Y" if ae.is_serious else "N",
                AEREL=rel,
                AEOUT=outcome,
            )
            out.append(row)
        return out

    def derive_vs(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects: Iterable[Subject],
        item_data_by_subject: dict[str, list[ItemData]],
        config: ItemMappingConfig,
    ) -> list[SdtmVs]:
        out: list[SdtmVs] = []
        for subj in subjects:
            seq = 0
            usubjid = _usubjid(study_id, subj.subject_code)
            for it in item_data_by_subject.get(subj.id, []):
                canonical = config.vs_item_map.get(it.item_id) or config.vs_item_map.get(
                    it.item_id.lower()
                )
                if not canonical:
                    continue
                spec = _VS_TEST_CODES.get(canonical)
                if not spec or it.value is None:
                    continue
                seq += 1
                out.append(
                    SdtmVs(
                        deployment_id=deployment_id,
                        STUDYID=study_id,
                        DOMAIN="VS",
                        USUBJID=usubjid,
                        VSSEQ=seq,
                        VSTESTCD=spec["VSTESTCD"],
                        VSTEST=spec["VSTEST"],
                        VSORRES=str(it.value),
                        VSORRESU=spec.get("default_unit"),
                        VSDTC=_to_iso8601(it.entered_at),
                    )
                )
        return out


__all__ = [
    "BuiltinPythonMapper",
    "CdiscMapper",
    "ItemMappingConfig",
]
