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
    FormInstance,
    ItemData,
    SdtmAe,
    SdtmCm,
    SdtmDm,
    SdtmEx,
    SdtmLb,
    SdtmMh,
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
_LB_TEST_CODES = _load_terminology("lb_test_codes.json")["mapping"]
_EX_ROUTES = _load_terminology("ex_routes.json")["mapping"]
_MH_CATEGORIES = _load_terminology("mh_categories.json")["mapping"]

# A form_instance feeding a repeating SDTM domain — bundled with its
# items for convenience. The pipeline gathers these via
# `_gather_form_instances_by_domain` and passes them to the new
# derivers (`derive_lb`, `derive_ex`, `derive_cm`, `derive_mh`).
FormInstanceWithItems = tuple[FormInstance, list[ItemData]]


# ── Item-mapping config ─────────────────────────────────────────────────


@dataclass
class ItemMappingConfig:
    """Per-deployment item-id → SDTM-variable overrides.

    Defaults below assume the form designer used conventional item ids
    (`age`, `sex`, `race`, `ethnic`, `sbp`, `dbp`, `hr`, `weight`,
    `height`, `temp`). For deployments using different conventions, pass
    `dm_item_map={'patient_age': 'AGE', ...}` or `vs_item_map={'bp_sys':
    'sbp', ...}` and the mapper will respect them.

    For repeating-form SDTM domains (LB / EX / CM / MH), the customer
    designates which `form_name` feeds which domain via
    `form_to_domain_map`. Each form-instance of that form becomes one
    SDTM row; the per-domain `*_item_map` then maps the FORM'S internal
    item ids onto the SDTM variables used in that domain.
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

    # ── Repeating-form domain selectors ──────────────────────────────────

    form_to_domain_map: dict[str, str] = field(
        default_factory=lambda: {
            # Defaults match the conventional form names a study designer
            # would pick. Override per-deployment if the customer uses
            # different form names.
            "lab_results": "LB",
            "labs": "LB",
            "exposure": "EX",
            "dose_admin": "EX",
            "concomitant_meds": "CM",
            "concomitant_medications": "CM",
            "medical_history": "MH",
        }
    )

    # ── LB: maps form item-ids → SDTM LB column or canonical test key. ──
    #
    # The form for a lab test typically captures three items per test:
    # the test result, its unit, and the collection date. Plus an
    # optional reference-range pair. The mapper looks for the special
    # keys `__test`, `__result`, `__unit`, `__date`, `__nrlo`, `__nrhi`
    # in this map to find the source-of-truth fields; or falls back to
    # the well-known names below.
    lb_item_map: dict[str, str] = field(
        default_factory=lambda: {
            "__test": "test",
            "__result": "result",
            "__unit": "unit",
            "__date": "collected_at",
            "__nrlo": "ref_low",
            "__nrhi": "ref_high",
        }
    )

    # ── EX: maps form item-ids → SDTM EX columns. ─────────────────────
    ex_item_map: dict[str, str] = field(
        default_factory=lambda: {
            "__trt": "treatment",
            "__dose": "dose",
            "__unit": "dose_unit",
            "__route": "route",
            "__start": "start_date",
            "__end": "end_date",
        }
    )

    # ── CM: maps form item-ids → SDTM CM columns. ─────────────────────
    cm_item_map: dict[str, str] = field(
        default_factory=lambda: {
            "__trt": "medication",
            "__decod": "atc_or_who",
            "__indication": "indication",
            "__dose": "dose",
            "__unit": "dose_unit",
            "__start": "start_date",
            "__end": "end_date",
        }
    )

    # ── MH: maps form item-ids → SDTM MH columns. ─────────────────────
    mh_item_map: dict[str, str] = field(
        default_factory=lambda: {
            "__term": "condition",
            "__decod": "meddra_pt",
            "__cat": "category",
            "__start": "onset_date",
            "__end": "resolved_date",
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
    ) -> list[SdtmDm]: ...

    def derive_ae(
        self,
        *,
        deployment_id: str,
        study_id: str,
        adverse_events: Iterable[AdverseEvent],
        subjects_by_id: dict[str, Subject] | None = None,
    ) -> list[SdtmAe]: ...

    def derive_vs(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects: Iterable[Subject],
        item_data_by_subject: dict[str, list[ItemData]],
        config: ItemMappingConfig,
    ) -> list[SdtmVs]: ...

    def derive_lb(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects_by_id: dict[str, Subject],
        form_instances: Iterable[FormInstanceWithItems],
        config: ItemMappingConfig,
    ) -> list[SdtmLb]: ...

    def derive_ex(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects_by_id: dict[str, Subject],
        form_instances: Iterable[FormInstanceWithItems],
        config: ItemMappingConfig,
    ) -> list[SdtmEx]: ...

    def derive_cm(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects_by_id: dict[str, Subject],
        form_instances: Iterable[FormInstanceWithItems],
        config: ItemMappingConfig,
    ) -> list[SdtmCm]: ...

    def derive_mh(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects_by_id: dict[str, Subject],
        form_instances: Iterable[FormInstanceWithItems],
        config: ItemMappingConfig,
    ) -> list[SdtmMh]: ...


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


def _try_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value))
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

    # ── Repeating-form derivers (LB / EX / CM / MH) ────────────────────

    def derive_lb(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects_by_id: dict[str, Subject],
        form_instances: Iterable[FormInstanceWithItems],
        config: ItemMappingConfig,
    ) -> list[SdtmLb]:
        """One row per lab measurement. Each form-instance of a form
        designated as the LB feed becomes a single LB row, identified
        by its `__test` item value (canonical key in lb_test_codes.json)."""
        out: list[SdtmLb] = []
        per_subject_seq: dict[str, int] = {}
        m = config.lb_item_map
        for fi, items in form_instances:
            subj = subjects_by_id.get(fi.subject_id)
            if subj is None:
                continue
            values: dict[str, str] = {it.item_id: it.value for it in items if it.value is not None}
            test_raw = values.get(m["__test"])
            if not test_raw:
                continue
            canonical = test_raw.strip().lower()
            spec = _LB_TEST_CODES.get(canonical) or _LB_TEST_CODES.get(test_raw.strip())
            if not spec:
                continue
            result_str = values.get(m["__result"])
            if result_str is None:
                continue
            result_num = _try_float(result_str)
            unit = values.get(m["__unit"]) or spec.get("default_unit")
            # Reference range — explicit values from the form win;
            # fall back to the CT default range so out-of-range flags
            # can be computed even when the form didn't capture one.
            nrlo = _try_float(values.get(m["__nrlo"]))
            nrhi = _try_float(values.get(m["__nrhi"]))
            if nrlo is None:
                nrlo = _try_float(spec.get("default_low"))
            if nrhi is None:
                nrhi = _try_float(spec.get("default_high"))
            nrind: str | None = None
            if result_num is not None and nrlo is not None and nrhi is not None:
                if result_num < nrlo:
                    nrind = "LOW"
                elif result_num > nrhi:
                    nrind = "HIGH"
                else:
                    nrind = "NORMAL"
            date_value = values.get(m["__date"])
            per_subject_seq[fi.subject_id] = per_subject_seq.get(fi.subject_id, 0) + 1
            out.append(
                SdtmLb(
                    deployment_id=deployment_id,
                    STUDYID=study_id,
                    DOMAIN="LB",
                    USUBJID=_usubjid(study_id, subj.subject_code),
                    LBSEQ=per_subject_seq[fi.subject_id],
                    LBTESTCD=spec["LBTESTCD"],
                    LBTEST=spec["LBTEST"],
                    LBORRES=result_str,
                    LBORRESU=unit,
                    LBSTRESC=result_str,
                    LBSTRESN=result_num,
                    LBSTRESU=unit,
                    LBORNRLO=str(nrlo) if nrlo is not None else None,
                    LBORNRHI=str(nrhi) if nrhi is not None else None,
                    LBSTNRLO=nrlo,
                    LBSTNRHI=nrhi,
                    LBNRIND=nrind,
                    LBDTC=_to_iso8601(date_value) if date_value else _to_iso8601(fi.created_at),
                )
            )
        return out

    def derive_lb_from_lab_results(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects_by_id: dict[str, Subject],
        lab_results: Iterable[Any],
        starting_seq_by_subject: dict[str, int] | None = None,
    ) -> list[SdtmLb]:
        """Emit one SDTM LB row per LabResult that links to a Subject.

        Pulls parsed-lab rows from the lab-data-feeds subsystem (P2 #6)
        into the SDTM bundle. LabResult rows without a linked subject
        (subject_id IS NULL) are skipped — the operator can backfill
        the link via the lab-results backfill endpoint and re-derive.

        `starting_seq_by_subject` lets the caller continue numbering
        after the form-based `derive_lb` ran, so LBSEQ stays unique
        per (deployment, USUBJID).

        Test-code lookup: prefers `_LB_TEST_CODES` for the parsed
        `test_code`, then falls back to canonicalising the
        `test_name`. Unknown tests still emit a row using the source
        code verbatim — better an imperfect SDTM row than a dropped
        observation.
        """
        out: list[SdtmLb] = []
        per_subject_seq: dict[str, int] = dict(starting_seq_by_subject or {})
        for r in lab_results:
            if r.subject_id is None:
                continue
            subj = subjects_by_id.get(r.subject_id)
            if subj is None:
                continue
            # Spec lookup: parsed test code first, then test name.
            spec = _LB_TEST_CODES.get(r.test_code.strip().lower()) if r.test_code else None
            if spec is None and r.test_name:
                spec = _LB_TEST_CODES.get(r.test_name.strip().lower())
            lbtestcd = spec["LBTESTCD"] if spec else (r.test_code or "")
            lbtest = spec["LBTEST"] if spec else (r.test_name or r.test_code or "")
            # Reference range: parsed range wins; fall back to the CT
            # default so out-of-range flags compute downstream.
            nrlo = r.ref_range_low
            nrhi = r.ref_range_high
            if spec is not None:
                if nrlo is None:
                    try:
                        nrlo = (
                            float(spec["default_low"])
                            if spec.get("default_low") is not None
                            else None
                        )
                    except (TypeError, ValueError):
                        nrlo = None
                if nrhi is None:
                    try:
                        nrhi = (
                            float(spec["default_high"])
                            if spec.get("default_high") is not None
                            else None
                        )
                    except (TypeError, ValueError):
                        nrhi = None
            # Reference-range flag: explicit `abnormal_flag` wins. The
            # parsers normalise H / HIGH variants into LBNRIND values
            # downstream; here we accept whatever the source gave us
            # and derive a 3-state indicator when numeric+range allow.
            nrind = r.abnormal_flag
            if nrind is None and r.value_numeric is not None:
                if nrlo is not None and r.value_numeric < nrlo:
                    nrind = "LOW"
                elif nrhi is not None and r.value_numeric > nrhi:
                    nrind = "HIGH"
                elif nrlo is not None or nrhi is not None:
                    nrind = "NORMAL"
            per_subject_seq[r.subject_id] = per_subject_seq.get(r.subject_id, 0) + 1
            usubjid = _usubjid(study_id, subj.subject_code)
            unit = r.units or (spec.get("default_unit") if spec else None)
            result_str = r.value_text or (
                str(r.value_numeric) if r.value_numeric is not None else None
            )
            out.append(
                SdtmLb(
                    deployment_id=deployment_id,
                    STUDYID=study_id,
                    DOMAIN="LB",
                    USUBJID=usubjid,
                    LBSEQ=per_subject_seq[r.subject_id],
                    LBTESTCD=lbtestcd,
                    LBTEST=lbtest,
                    LBORRES=result_str,
                    LBORRESU=unit,
                    LBSTRESC=result_str,
                    LBSTRESN=r.value_numeric,
                    LBSTRESU=unit,
                    LBORNRLO=str(nrlo) if nrlo is not None else None,
                    LBORNRHI=str(nrhi) if nrhi is not None else None,
                    LBSTNRLO=nrlo,
                    LBSTNRHI=nrhi,
                    LBNRIND=nrind,
                    LBDTC=_to_iso8601(r.collected_at) if r.collected_at else None,
                )
            )
        return out

    def derive_ex(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects_by_id: dict[str, Subject],
        form_instances: Iterable[FormInstanceWithItems],
        config: ItemMappingConfig,
    ) -> list[SdtmEx]:
        out: list[SdtmEx] = []
        per_subject_seq: dict[str, int] = {}
        m = config.ex_item_map
        for fi, items in form_instances:
            subj = subjects_by_id.get(fi.subject_id)
            if subj is None:
                continue
            values: dict[str, str] = {it.item_id: it.value for it in items if it.value is not None}
            trt = values.get(m["__trt"])
            if not trt:
                continue
            per_subject_seq[fi.subject_id] = per_subject_seq.get(fi.subject_id, 0) + 1
            route_raw = values.get(m["__route"])
            route = _ct_lookup(_EX_ROUTES, route_raw) if route_raw else None
            out.append(
                SdtmEx(
                    deployment_id=deployment_id,
                    STUDYID=study_id,
                    DOMAIN="EX",
                    USUBJID=_usubjid(study_id, subj.subject_code),
                    EXSEQ=per_subject_seq[fi.subject_id],
                    EXTRT=trt,
                    EXDOSE=_try_float(values.get(m["__dose"])),
                    EXDOSU=values.get(m["__unit"]),
                    EXROUTE=route or route_raw,
                    EXSTDTC=_to_iso8601(values.get(m["__start"]) or fi.created_at),
                    EXENDTC=_to_iso8601(values.get(m["__end"])),
                )
            )
        return out

    def derive_cm(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects_by_id: dict[str, Subject],
        form_instances: Iterable[FormInstanceWithItems],
        config: ItemMappingConfig,
    ) -> list[SdtmCm]:
        out: list[SdtmCm] = []
        per_subject_seq: dict[str, int] = {}
        m = config.cm_item_map
        for fi, items in form_instances:
            subj = subjects_by_id.get(fi.subject_id)
            if subj is None:
                continue
            values: dict[str, str] = {it.item_id: it.value for it in items if it.value is not None}
            trt = values.get(m["__trt"])
            if not trt:
                continue
            per_subject_seq[fi.subject_id] = per_subject_seq.get(fi.subject_id, 0) + 1
            out.append(
                SdtmCm(
                    deployment_id=deployment_id,
                    STUDYID=study_id,
                    DOMAIN="CM",
                    USUBJID=_usubjid(study_id, subj.subject_code),
                    CMSEQ=per_subject_seq[fi.subject_id],
                    CMTRT=trt,
                    CMDECOD=values.get(m["__decod"]),
                    CMINDC=values.get(m["__indication"]),
                    CMDOSE=_try_float(values.get(m["__dose"])),
                    CMDOSU=values.get(m["__unit"]),
                    CMSTDTC=_to_iso8601(values.get(m["__start"])),
                    CMENDTC=_to_iso8601(values.get(m["__end"])),
                )
            )
        return out

    def derive_mh(
        self,
        *,
        deployment_id: str,
        study_id: str,
        subjects_by_id: dict[str, Subject],
        form_instances: Iterable[FormInstanceWithItems],
        config: ItemMappingConfig,
    ) -> list[SdtmMh]:
        out: list[SdtmMh] = []
        per_subject_seq: dict[str, int] = {}
        m = config.mh_item_map
        for fi, items in form_instances:
            subj = subjects_by_id.get(fi.subject_id)
            if subj is None:
                continue
            values: dict[str, str] = {it.item_id: it.value for it in items if it.value is not None}
            term = values.get(m["__term"])
            if not term:
                continue
            per_subject_seq[fi.subject_id] = per_subject_seq.get(fi.subject_id, 0) + 1
            end_dtc = _to_iso8601(values.get(m["__end"]))
            cat_raw = values.get(m["__cat"])
            cat = _ct_lookup(_MH_CATEGORIES, cat_raw) if cat_raw else None
            out.append(
                SdtmMh(
                    deployment_id=deployment_id,
                    STUDYID=study_id,
                    DOMAIN="MH",
                    USUBJID=_usubjid(study_id, subj.subject_code),
                    MHSEQ=per_subject_seq[fi.subject_id],
                    MHTERM=term,
                    MHDECOD=values.get(m["__decod"]),
                    MHCAT=cat or cat_raw,
                    MHSTDTC=_to_iso8601(values.get(m["__start"])),
                    MHENDTC=end_dtc,
                    MHONGO="Y" if not end_dtc else "N",
                )
            )
        return out


__all__ = [
    "BuiltinPythonMapper",
    "CdiscMapper",
    "FormInstanceWithItems",
    "ItemMappingConfig",
]
