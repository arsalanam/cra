"""Lab unit standardisation for the SDTM LB domain.

By default the LB deriver emits ``LBSTRESN = LBORRES`` (no conversion), so a
lab reported in US-conventional units (mg/dL, g/dL) never harmonises with one
reported in SI (mmol/L, µmol/L, g/L). `standardize_lab` applies a per-analyte
conversion so the standardised columns (LBSTRESN / LBSTRESU and the matching
LBSTNRLO / LBSTNRHI range limits) are all in one system — which is what makes
the LB dataset poolable across sites and regulator-ready.

The table below is a sensible default for common clinical-chemistry analytes;
extend `_LB_STD_CONVERSIONS` at deploy for a site's own panel. Only the
listed (analyte, from-unit) pairs convert; anything else — including a value
already in the standard unit, an enzyme in U/L, or a percentage — passes
through unchanged.
"""

from __future__ import annotations

# LBTESTCD → (from-unit, standard-unit, multiply factor).  US-conventional →
# SI for the analytes where the two systems differ. Electrolytes (K, NA, CL)
# are mEq/L == mmol/L already; enzymes / percentages / cell counts have no
# standard conversion and are omitted.
_LB_STD_CONVERSIONS: dict[str, tuple[str, str, float]] = {
    "GLUC": ("mg/dL", "mmol/L", 0.0555),
    "CHOL": ("mg/dL", "mmol/L", 0.02586),
    "HDL": ("mg/dL", "mmol/L", 0.02586),
    "LDL": ("mg/dL", "mmol/L", 0.02586),
    "TRIG": ("mg/dL", "mmol/L", 0.01129),
    "CREAT": ("mg/dL", "umol/L", 88.42),
    "BUN": ("mg/dL", "mmol/L", 0.357),
    "BILI": ("mg/dL", "umol/L", 17.10),
    "CA": ("mg/dL", "mmol/L", 0.2495),
    "HGB": ("g/dL", "g/L", 10.0),
    "ALB": ("g/dL", "g/L", 10.0),
}


def _norm_unit(unit: str | None) -> str:
    """Case/format-insensitive unit key (µ→u, strip spaces)."""
    if not unit:
        return ""
    return unit.strip().lower().replace("µ", "u").replace(" ", "")


def _convert(testcd: str | None, value: float | None, unit: str | None) -> tuple[float | None, str]:
    """Convert one value to the analyte's standard unit; identity when no
    conversion applies. Returns (std_value, std_unit)."""
    if value is None:
        return None, unit or ""
    conv = _LB_STD_CONVERSIONS.get((testcd or "").upper())
    if conv is None:
        return value, unit or ""
    from_u, to_u, factor = conv
    if _norm_unit(unit) != _norm_unit(from_u):
        # Already standard, or an unrecognised source unit — don't touch it.
        return value, unit or ""
    return round(value * factor, 6), to_u


def standardize_lab(
    *,
    testcd: str | None,
    value: float | None,
    unit: str | None,
    nrlo: float | None,
    nrhi: float | None,
) -> tuple[float | None, str, float | None, float | None]:
    """Standardise a lab result + its normal range together.

    Returns (LBSTRESN, LBSTRESU, LBSTNRLO, LBSTNRHI). The range limits use
    the SAME conversion as the value so LBNRIND stays consistent in the
    standardised system.
    """
    std_value, std_unit = _convert(testcd, value, unit)
    std_nrlo, _ = _convert(testcd, nrlo, unit)
    std_nrhi, _ = _convert(testcd, nrhi, unit)
    return std_value, std_unit, std_nrlo, std_nrhi


__all__ = ["standardize_lab"]
