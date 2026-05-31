"""Frequentist network meta-analysis — sandbox-side.

Runs inside the Docker sandbox. Reads ``/home/sandbox/input/data.json``::

    {
      "data": {
        "studies": [
          {"source_id": "12345", "arms": [
            {"intervention": "Drug A", "n": 200, "events": 40},
            {"intervention": "Placebo", "n": 198, "events": 62}
          ]},
          ...
        ],
        "interventions": ["Placebo", "Drug A", "Drug B", "Drug C"],
        "effect_measure": "OR"   // OR | RR | MD | SMD | HR
      }
    }

Writes to ``/home/sandbox/output/``:
  • ``nma-league-frequentist.json`` — league table + SUCRA + network
    metadata for the host to assemble into the NmaResults schema.

Method:

  Each study contributes one or more arm-level "contrasts" against its
  reference arm (first non-reference arm in the study). For binary
  endpoints (OR/RR), each contrast is the log-OR/log-RR + variance using
  Haldane-Anscombe 0.5 for zero cells. For continuous (MD/SMD),
  contrasts are the mean difference + variance computed from per-arm
  SDs + ns.

  All contrasts are stacked into a (#contrasts, #interventions-1)
  design matrix X and a y = log-effect vector with weights w = 1/var.
  The pooled estimate β = (X^T W X)^{-1} X^T W y gives the all-vs-
  reference log-effects; the inverse of the information matrix is the
  variance-covariance matrix. League cells for non-reference pairs
  (i, j) are computed as β_i − β_j with variance σ²_i + σ²_j − 2cov(i, j)
  from the inverse Fisher information.

  SUCRA is computed by simulating 1000 draws from the multivariate
  normal posterior of β, ranking each draw, and averaging the
  cumulative ranking probability per intervention.

  This is the electrical-network analogy approach (Lu & Ades 2004,
  Salanti et al. 2008) implemented directly on top of numpy + scipy
  (no netmeta R dependency).
"""

from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

import numpy as np
from scipy.stats import multivariate_normal, norm

_INPUT = Path("/home/sandbox/input/data.json")
_OUTPUT_DIR = Path("/home/sandbox/output")
_HALDANE = 0.5
_DRAWS_FOR_SUCRA = 1000


def _binary_contrast(arm_a: dict, arm_b: dict, effect_measure: str) -> tuple[float, float] | None:
    """Return (log_effect, variance) for one binary contrast (arm_a vs arm_b)."""
    e_a = (arm_a.get("events") or 0) + _HALDANE
    n_a = arm_a.get("n") or 0
    f_a = (n_a - (arm_a.get("events") or 0)) + _HALDANE
    e_b = (arm_b.get("events") or 0) + _HALDANE
    n_b = arm_b.get("n") or 0
    f_b = (n_b - (arm_b.get("events") or 0)) + _HALDANE
    if n_a <= 0 or n_b <= 0:
        return None
    if effect_measure == "OR":
        try:
            log_eff = math.log((e_a * f_b) / (e_b * f_a))
        except (ValueError, ZeroDivisionError):
            return None
        var = 1 / e_a + 1 / f_a + 1 / e_b + 1 / f_b
        return log_eff, var
    if effect_measure in ("RR", "HR"):
        # HR computed from a Cox-like approximation when only event counts available.
        try:
            log_eff = math.log((e_a / n_a) / (e_b / n_b))
        except (ValueError, ZeroDivisionError):
            return None
        var = (1 / e_a - 1 / n_a) + (1 / e_b - 1 / n_b)
        return log_eff, var
    return None


def _continuous_contrast(
    arm_a: dict, arm_b: dict, effect_measure: str
) -> tuple[float, float] | None:
    m_a = arm_a.get("mean")
    sd_a = arm_a.get("sd")
    n_a = arm_a.get("n") or 0
    m_b = arm_b.get("mean")
    sd_b = arm_b.get("sd")
    n_b = arm_b.get("n") or 0
    if None in (m_a, sd_a, m_b, sd_b) or n_a <= 0 or n_b <= 0:
        return None
    if effect_measure == "MD":
        diff = float(m_a - m_b)
        var = sd_a**2 / n_a + sd_b**2 / n_b
        return diff, var
    if effect_measure == "SMD":
        # Hedges' g
        pooled_sd = math.sqrt(((n_a - 1) * sd_a**2 + (n_b - 1) * sd_b**2) / max(n_a + n_b - 2, 1))
        if pooled_sd == 0:
            return None
        g = (m_a - m_b) / pooled_sd
        var = (n_a + n_b) / (n_a * n_b) + g**2 / (2 * (n_a + n_b))
        return g, var
    return None


def _contrast(arm_a: dict, arm_b: dict, effect_measure: str) -> tuple[float, float] | None:
    if effect_measure in ("OR", "RR", "HR"):
        return _binary_contrast(arm_a, arm_b, effect_measure)
    return _continuous_contrast(arm_a, arm_b, effect_measure)


def _is_log_scale(effect_measure: str) -> bool:
    return effect_measure in ("OR", "RR", "HR")


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_INPUT.read_text(encoding="utf-8"))
    data = payload.get("data") or {}
    studies = data.get("studies") or []
    interventions = data.get("interventions") or []
    effect_measure = str(data.get("effect_measure", "OR"))
    if len(interventions) < 3 or len(studies) < 2:
        (_OUTPUT_DIR / "nma-league-frequentist.json").write_text(
            json.dumps(
                {
                    "fitted": False,
                    "skip_reason": ("NMA requires ≥3 interventions and ≥2 studies."),
                    "n_interventions": len(interventions),
                    "n_studies": len(studies),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    reference = interventions[0]
    others = interventions[1:]
    index_of = {name: i for i, name in enumerate(interventions)}

    # Build contrasts vs each study's first arm (or vs reference if reference
    # is in that study). y = log-effects, X = design matrix with one column
    # per non-reference intervention, W = diagonal weight matrix.
    y_rows: list[float] = []
    x_rows: list[list[float]] = []
    w_diag: list[float] = []
    edge_counts: dict[tuple[str, str], int] = {}
    node_studies: dict[str, int] = {name: 0 for name in interventions}
    node_participants: dict[str, int] = {name: 0 for name in interventions}
    included_source_ids: list[str] = []

    for s in studies:
        arms_raw = s.get("arms") or []
        arms = [a for a in arms_raw if a.get("intervention") in index_of]
        if len(arms) < 2:
            continue
        # Pick a within-study baseline arm: prefer reference; else first arm.
        baseline = next((a for a in arms if a.get("intervention") == reference), arms[0])
        baseline_name = baseline.get("intervention")
        for arm in arms:
            if arm is baseline:
                continue
            contrast = _contrast(arm, baseline, effect_measure)
            if contrast is None:
                continue
            eff, var = contrast
            if var <= 0:
                continue
            row = [0.0] * len(others)
            arm_name = arm.get("intervention")
            # X column for non-reference arm: +1 for arm, −1 for baseline
            # (when baseline is non-reference, subtract its column).
            if arm_name != reference and arm_name in others:
                row[others.index(arm_name)] = 1.0
            if baseline_name != reference and baseline_name in others:
                row[others.index(baseline_name)] = -1.0
            y_rows.append(eff)
            x_rows.append(row)
            w_diag.append(1.0 / var)
            # Track edge for network plot.
            pair = tuple(sorted([baseline_name, arm_name]))  # type: ignore[arg-type]
            edge_counts[pair] = edge_counts.get(pair, 0) + 1
            node_studies[baseline_name] = node_studies.get(baseline_name, 0) + 1  # type: ignore[arg-type]
            node_studies[arm_name] = node_studies.get(arm_name, 0) + 1  # type: ignore[arg-type]
            node_participants[baseline_name] = node_participants.get(baseline_name, 0) + int(
                baseline.get("n") or 0
            )
            node_participants[arm_name] = node_participants.get(arm_name, 0) + int(
                arm.get("n") or 0
            )
        if s.get("source_id"):
            included_source_ids.append(str(s["source_id"]))

    if not y_rows:
        (_OUTPUT_DIR / "nma-league-frequentist.json").write_text(
            json.dumps(
                {
                    "fitted": False,
                    "skip_reason": "No valid contrasts could be extracted.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    y = np.array(y_rows, dtype=float)
    x = np.array(x_rows, dtype=float)
    w = np.diag(w_diag)
    # β = (X^T W X)^{-1} X^T W y
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            xt_w = x.T @ w
            cov = np.linalg.inv(xt_w @ x)
            beta = cov @ xt_w @ y
    except np.linalg.LinAlgError as e:
        (_OUTPUT_DIR / "nma-league-frequentist.json").write_text(
            json.dumps(
                {
                    "fitted": False,
                    "skip_reason": f"Design matrix is singular: {e}",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    log_scale = _is_log_scale(effect_measure)
    # Build league table: pairwise effects (i vs j) = β_i − β_j (in log-
    # scale for binary measures). β indices map to `others`; reference has
    # β = 0 implicitly.
    pos_of = {name: i for i, name in enumerate(others)}
    league_rows: list[dict] = []
    for i, row_name in enumerate(interventions):
        for j, col_name in enumerate(interventions):
            if i == j:
                continue
            if row_name == reference:
                diff = -beta[pos_of[col_name]]
                var_diff = cov[pos_of[col_name], pos_of[col_name]]
            elif col_name == reference:
                diff = beta[pos_of[row_name]]
                var_diff = cov[pos_of[row_name], pos_of[row_name]]
            else:
                ri = pos_of[row_name]
                ci = pos_of[col_name]
                diff = beta[ri] - beta[ci]
                var_diff = cov[ri, ri] + cov[ci, ci] - 2 * cov[ri, ci]
            se_diff = float(math.sqrt(max(var_diff, 0)))
            z = float(norm.ppf(0.975))
            lo = float(diff - z * se_diff)
            hi = float(diff + z * se_diff)
            effect = float(math.exp(diff)) if log_scale else float(diff)
            ci_lo = float(math.exp(lo)) if log_scale else lo
            ci_hi = float(math.exp(hi)) if log_scale else hi
            n_direct = edge_counts.get(tuple(sorted([row_name, col_name])), 0)
            league_rows.append(
                {
                    "row_intervention": row_name,
                    "col_intervention": col_name,
                    "effect": effect,
                    "ci_lower": ci_lo,
                    "ci_upper": ci_hi,
                    "n_direct_trials": int(n_direct),
                    "n_indirect_paths": 0,  # not enumerated; conservative 0
                }
            )

    # SUCRA via multivariate-normal posterior draws.
    rng = np.random.default_rng(seed=0)
    full_beta = np.concatenate([[0.0], beta])
    full_cov = np.zeros((len(interventions), len(interventions)))
    for i, ni in enumerate(others, start=1):
        for j, nj in enumerate(others, start=1):
            full_cov[i, j] = cov[pos_of[ni], pos_of[nj]]
    try:
        draws = multivariate_normal.rvs(
            mean=full_beta,
            cov=full_cov,
            size=_DRAWS_FOR_SUCRA,
            random_state=rng,
        )
    except (ValueError, np.linalg.LinAlgError) as e:
        draws = np.tile(full_beta, (1, 1))
        sucra_note = f"SUCRA fallback: {type(e).__name__}: {e}"
    else:
        sucra_note = None
    if draws.ndim == 1:
        draws = draws.reshape(1, -1)
    # Convention: "better" = larger SUCRA. For OR/RR/HR/MD/SMD where lower is
    # better (treatment reduces risk / event / size), invert ordering by using
    # ranks on -draws. Here we assume "favours_intervention" is lower draw —
    # the host should reverse the SUCRA if higher-is-better was the convention.
    ranks = np.argsort(np.argsort(-draws, axis=1), axis=1) + 1  # rank 1 = best
    mean_ranks = ranks.mean(axis=0)
    sucra_values = ((len(interventions) - mean_ranks) / max(len(interventions) - 1, 1)).tolist()
    sucra_table = [
        {
            "intervention": name,
            "sucra": float(sucra_values[i]),
            "mean_rank": float(mean_ranks[i]),
            "rank": int(np.argsort(np.argsort(-np.array(sucra_values)))[i] + 1),
        }
        for i, name in enumerate(interventions)
    ]

    network_nodes = [
        {
            "intervention": name,
            "n_studies": int(node_studies.get(name, 0)),
            "n_participants": int(node_participants.get(name, 0)),
        }
        for name in interventions
    ]
    network_edges = [
        {
            "source_intervention": pair[0],
            "target_intervention": pair[1],
            "n_trials": int(n),
        }
        for pair, n in edge_counts.items()
    ]

    out = {
        "fitted": True,
        "backend": "frequentist",
        "effect_measure": effect_measure,
        "reference": reference,
        "league_table": league_rows,
        "sucra": sucra_table,
        "network": {"nodes": network_nodes, "edges": network_edges},
        "n_studies": len({str(s.get("source_id")) for s in studies if s.get("source_id")}),
        "n_contrasts": len(y_rows),
        "included_source_ids": included_source_ids,
    }
    if sucra_note:
        out["sucra_note"] = sucra_note
    (_OUTPUT_DIR / "nma-league-frequentist.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    print(
        f"[nma_frequentist] backend=frequentist effect={effect_measure} "
        f"interventions={len(interventions)} studies={out['n_studies']} "
        f"contrasts={len(y_rows)}"
    )


if __name__ == "__main__":
    main()
