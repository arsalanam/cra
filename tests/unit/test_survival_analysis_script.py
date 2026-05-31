"""Run the survival-analysis sandbox script directly (no Docker).

The script lives in `cdisc.sandbox_scripts.survival_analysis` and is
read by the survival endpoint to ship to the sandbox at run time. We
import it as a normal module here and overwrite its two hardcoded
paths (`_INPUT` / `_OUTPUT_DIR`) to a tmp path. This validates the
script's logic without the Docker round-trip; the endpoint test
(test_survival_endpoint.py) covers the sandbox wiring with the
sandbox mocked.

Skipped automatically if pandas / numpy / matplotlib / statsmodels
aren't importable on the host (they're sandbox-tier deps, not host-
tier — but the dev venv pins all of them, so the test runs by
default in CI).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")
pytest.importorskip("matplotlib")
pytest.importorskip("statsmodels")


from research_assistant.cdisc.sandbox_scripts import survival_analysis  # noqa: E402


def _payload_two_arms() -> dict:
    """20 subjects across 2 arms with mixed events + censoring."""
    rows = []
    for i in range(10):
        rows.append(
            {
                "USUBJID": f"S-A{i:03d}",
                "PARAMCD": "TTAE",
                "PARAM": "Time to First AE",
                "AVAL": 10.0 + i * 2.0,
                "CNSR": 0 if i % 2 == 0 else 1,
                "TRT01A": "Drug A",
            }
        )
    for i in range(10):
        rows.append(
            {
                "USUBJID": f"S-B{i:03d}",
                "PARAMCD": "TTAE",
                "PARAM": "Time to First AE",
                "AVAL": 5.0 + i * 1.5,
                "CNSR": 0 if i % 3 != 0 else 1,
                "TRT01A": "Drug B",
            }
        )
    return {"adtte": rows, "adsl": []}


def test_script_produces_km_png_and_cox_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inp = tmp_path / "input"
    out = tmp_path / "output"
    inp.mkdir()
    out.mkdir()
    (inp / "data.json").write_text(json.dumps(_payload_two_arms()), encoding="utf-8")
    monkeypatch.setattr(survival_analysis, "_INPUT", inp / "data.json")
    monkeypatch.setattr(survival_analysis, "_OUTPUT_DIR", out)

    survival_analysis.main()

    km_files = list(out.glob("km-*.png"))
    assert len(km_files) == 1
    assert km_files[0].name == "km-ttae.png"
    cox_file = out / "cox-summary.json"
    assert cox_file.exists()
    cox = json.loads(cox_file.read_text(encoding="utf-8"))
    [param] = cox["params"]
    assert param["fitted"] is True
    assert param["paramcd"] == "TTAE"
    [row] = param["rows"]
    assert row["comparison"] == "Drug B vs Drug A"
    assert 0.0 < row["hr"] < 100.0
    assert row["hr_95ci_lo"] < row["hr"] < row["hr_95ci_hi"]


def test_script_skips_cox_on_single_arm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inp = tmp_path / "input"
    out = tmp_path / "output"
    inp.mkdir()
    out.mkdir()
    rows = [
        {
            "USUBJID": f"S{i:03d}",
            "PARAMCD": "TTAE",
            "PARAM": "Time to First AE",
            "AVAL": float(10 + i),
            "CNSR": i % 2,
            "TRT01A": "Drug A",
        }
        for i in range(10)
    ]
    (inp / "data.json").write_text(json.dumps({"adtte": rows, "adsl": []}), encoding="utf-8")
    monkeypatch.setattr(survival_analysis, "_INPUT", inp / "data.json")
    monkeypatch.setattr(survival_analysis, "_OUTPUT_DIR", out)

    survival_analysis.main()
    cox = json.loads((out / "cox-summary.json").read_text(encoding="utf-8"))
    [param] = cox["params"]
    assert param["fitted"] is False
    assert "Single treatment arm" in param["skip_reason"]


def test_script_skips_cox_when_too_few_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inp = tmp_path / "input"
    out = tmp_path / "output"
    inp.mkdir()
    out.mkdir()
    rows = [
        {
            "USUBJID": f"S-A{i:03d}",
            "PARAMCD": "TTAE",
            "PARAM": "T",
            "AVAL": 5.0,
            "CNSR": 1,
            "TRT01A": "Drug A",
        }
        for i in range(5)
    ] + [
        {
            "USUBJID": f"S-B{i:03d}",
            "PARAMCD": "TTAE",
            "PARAM": "T",
            "AVAL": 5.0,
            "CNSR": 1,
            "TRT01A": "Drug B",
        }
        for i in range(5)
    ]
    (inp / "data.json").write_text(json.dumps({"adtte": rows, "adsl": []}), encoding="utf-8")
    monkeypatch.setattr(survival_analysis, "_INPUT", inp / "data.json")
    monkeypatch.setattr(survival_analysis, "_OUTPUT_DIR", out)

    survival_analysis.main()
    cox = json.loads((out / "cox-summary.json").read_text(encoding="utf-8"))
    [param] = cox["params"]
    assert param["fitted"] is False
    assert "Too few events" in param["skip_reason"]


def test_script_empty_input_emits_note_not_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inp = tmp_path / "input"
    out = tmp_path / "output"
    inp.mkdir()
    out.mkdir()
    (inp / "data.json").write_text(json.dumps({"adtte": [], "adsl": []}), encoding="utf-8")
    monkeypatch.setattr(survival_analysis, "_INPUT", inp / "data.json")
    monkeypatch.setattr(survival_analysis, "_OUTPUT_DIR", out)

    survival_analysis.main()
    cox = json.loads((out / "cox-summary.json").read_text(encoding="utf-8"))
    assert cox["params"] == []
    assert "No ADTTE rows" in cox["note"]
