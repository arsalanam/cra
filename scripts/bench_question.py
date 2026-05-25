"""End-to-end timing + token-tracking benchmark for one user question.

Fires the question via /api/turn, times the response, then verifies that
input + output tokens were captured AND that the daily usage delta on
/api/threads/usage/today matches what the turn reported. Confirms the
tracking pipeline (specialist.run_turn → meta → done event → quota
aggregator → /usage/today) is intact end to end.

Run:
    uv run --with httpx python scripts/bench_question.py

Assumes the FastAPI dev server is running on http://localhost:8000.
"""

from __future__ import annotations

import sys
import time

import httpx

BASE = "http://localhost:8000"
QUESTION = (
    "Which US state or combination of states' GDP sums up to become "
    "GDP of entire country of Pakistan?"
)
TIMEOUT_S = 360  # client-side cap, must exceed agent_timeout_seconds


def _hr(label: str) -> None:
    print(f"\n-- {label} " + "-" * max(0, 78 - len(label) - 4))


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=httpx.Timeout(TIMEOUT_S))

    _hr("server health")
    h = client.get("/api/health")
    h.raise_for_status()
    health = h.json()
    print(f"model:  {health['model']}")
    print(f"region: {health['region']}")

    _hr("usage BEFORE")
    before = client.get("/api/threads/usage/today").json()
    print(f"input_used:  {before['input_tokens']['used']:>10,}")
    print(f"output_used: {before['output_tokens']['used']:>10,}")
    print(f"enforcement: {before['enforcement_enabled']}")

    _hr("create thread")
    t = client.post("/api/threads", json={"title": "bench/pakistan-gdp"})
    t.raise_for_status()
    thread_id = t.json()["id"]
    print(f"thread_id: {thread_id}")
    print(f"question:  {QUESTION}")

    _hr("dispatch turn — timing")
    t0 = time.monotonic()
    try:
        r = client.post(
            "/api/turn",
            json={"thread_id": thread_id, "user_message": QUESTION},
        )
        elapsed = time.monotonic() - t0
        status_code = r.status_code
        is_json = r.headers.get("content-type", "").startswith("application/json")
        body = r.json() if is_json else {"raw": r.text}
    except httpx.HTTPError as e:
        elapsed = time.monotonic() - t0
        print(f"HTTP error after {elapsed:.1f}s: {type(e).__name__}: {e}")
        return 2

    print(f"status:  {status_code}")
    print(f"elapsed: {elapsed:.1f}s")

    _hr("response payload")
    if status_code == 200:
        print(f"workflow: {body['workflow']}")
        usage = body.get("usage", {})
        quota = body.get("quota", {})
        print(f"usage.input_tokens:  {usage.get('input_tokens', 0):>10,}")
        print(f"usage.output_tokens: {usage.get('output_tokens', 0):>10,}")
        print(f"usage.requests:      {usage.get('requests', 0):>10,}")
        print(f"usage.tool_calls:    {usage.get('tool_calls', 0):>10,}")
        print(f"quota.input.percent:  {quota.get('input_tokens', {}).get('percent', 0):>6}%")
        print(f"quota.output.percent: {quota.get('output_tokens', {}).get('percent', 0):>6}%")

        # Print a snippet of the answer
        out = body.get("output", {})
        kind = out.get("kind", "?")
        print(f"\noutput.kind: {kind}")
        text = out.get("text") or out.get("answer") or ""
        if text:
            preview = text[:600].replace("\n", " ")
            print(f"answer preview: {preview}{'…' if len(text) > 600 else ''}")
    else:
        print(f"error: {body.get('detail', body)}")

    _hr("usage AFTER")
    after = client.get("/api/threads/usage/today").json()
    print(f"input_used:  {after['input_tokens']['used']:>10,}")
    print(f"output_used: {after['output_tokens']['used']:>10,}")

    _hr("verification")
    in_delta = after["input_tokens"]["used"] - before["input_tokens"]["used"]
    out_delta = after["output_tokens"]["used"] - before["output_tokens"]["used"]
    print(f"input delta:  {in_delta:>10,}")
    print(f"output delta: {out_delta:>10,}")

    if status_code == 200:
        usage = body.get("usage", {})
        in_match = in_delta == usage.get("input_tokens", 0)
        out_match = out_delta == usage.get("output_tokens", 0)
        print(f"input match (delta == TurnResponse.usage):  {in_match}")
        print(f"output match (delta == TurnResponse.usage): {out_match}")
        if not (in_match and out_match):
            print("WARN: usage on the response does not match the today-totals delta")
            return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
