# ReACT Agent Tutorial — Pydantic AI + AWS Bedrock

> **Interactive tutorial** teaching the ReACT (Reasoning + Acting) pattern using
> [Pydantic AI](https://ai.pydantic.dev/), AWS Bedrock (Claude Sonnet), and
> [GAIA benchmark](https://huggingface.co/datasets/gaia-benchmark/GAIA)-style questions.

![Pattern](https://img.shields.io/badge/Pattern-ReACT-00c8f8?style=flat-square)
![Framework](https://img.shields.io/badge/Framework-Pydantic%20AI-f0a500?style=flat-square)
![LLM](https://img.shields.io/badge/LLM-AWS%20Bedrock-00e09a?style=flat-square)
![Package](https://img.shields.io/badge/Managed%20by-uv-4b3fbb?style=flat-square)

---

## What You'll Learn

| Concept | Where |
|---|---|
| ReACT loop: Thought → Action → Observe → Answer | `agent/prompts.py` |
| Pydantic AI `Agent` with Bedrock model | `agent/model.py`, `agent/react.py` |
| `@agent.tool` + `RunContext[Deps]` dependency injection | `tools/*.py`, `agent/deps.py` |
| Streaming every model turn via `agent.iter()` | `agent/runner.py` |
| Pure `_impl` + `register()` tool pattern (unit-testable) | `tools/*.py` |
| FastAPI + SSE (Server-Sent Events) for real-time UI | `web/app.py`, `web/sse.py` |
| Modern Python: `uv`, `src/` layout, `ruff`, `mypy`, `pytest` | `pyproject.toml` |

---

## Quick Start

### 1. Prerequisites
- [uv](https://docs.astral.sh/uv/) (`pip install uv` or see their install docs)
- Python 3.13 (uv will fetch it if missing)
- AWS account with Bedrock model access enabled

### 2. Install

```bash
uv sync --all-extras
```

### 3. Configure AWS (edit `.env`)

```env
AWS_PROFILE=default                                    # or use keys below
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6        # pick from `aws bedrock list-inference-profiles`
APP_PORT=8000
LOG_LEVEL=info
```

Enable Bedrock model access at **AWS Console → Bedrock → Model Access**.

### 4. Run

```bash
uv run research-assistant      # starts FastAPI on :8000 with reload
# Equivalent: uv run python -m pydantic
```

Open **http://localhost:8000**.

### 5. Dev loop

```bash
uv run pytest                       # run tests (stubs today — fill them in!)
uv run ruff check src tests         # lint
uv run ruff format src tests        # format
uv run mypy src                     # type-check
```

---

## Project Structure

```
research-assistant/
├── pyproject.toml              # uv deps, ruff, mypy, pytest config
├── .python-version             # 3.13
├── .env                        # AWS creds + model config (not committed)
├── README.md
│
├── src/research_assistant/
│   ├── __main__.py             # uv run research-assistant → here
│   ├── config.py               # pydantic-settings Settings class
│   │
│   ├── agent/                  ◀ Core teaching code — START HERE
│   │   ├── model.py            # Bedrock model + provider wiring
│   │   ├── deps.py             # AgentDeps dataclass (event queue, file)
│   │   ├── prompts.py          # ReACT system prompt
│   │   ├── react.py            # build_agent() — registers all tools
│   │   └── runner.py           # run_react_stream() — agent.iter() streaming
│   │
│   ├── tools/                  ◀ One file per tool, _impl + register()
│   │   ├── web_search.py       # DuckDuckGo (no API key)
│   │   ├── wikipedia.py        # Wikipedia API
│   │   ├── calculator.py       # AST-whitelist safe eval
│   │   ├── python_repl.py      # Sandboxed exec with import whitelist
│   │   └── read_file.py        # txt/csv/json parser
│   │
│   ├── web/
│   │   ├── app.py              # FastAPI create_app() factory
│   │   ├── sse.py              # format_sse() helper
│   │   └── static/index.html   # React (CDN Babel, no build step)
│   │
│   └── data/sample_questions.py
│
└── tests/                      ◀ Currently STUBS — see § Tests
    ├── unit/
    └── integration/
```

---

## The ReACT Pattern

ReACT = **Rea**soning + a**ct**ing. The model alternates:

```
Thought:     I need to calculate the ISS orbital speed.
Action:      calculator("2 * pi * (6371 + 408) / 92 * 60")
Observation: 27579.47...
Final Answer: The ISS orbits at approximately 27,579 km/h.
```

The loop continues until the model has enough information for a final answer.
The system prompt in `agent/prompts.py` instructs the model to use this exact
formatting — which the frontend then parses and highlights.

### Why `agent.iter()` instead of `run_stream()`?

`run_stream()` + `stream_text()` only stream the **final** model response.
When the model produces intermediate reasoning ("Thought:") *before* calling a
tool, that text never reaches the UI. `agent.iter()` walks every node in the
agent graph — model request nodes, tool-call nodes, end node — so we can stream
every turn. See `agent/runner.py`.

---

## Tool Pattern

Each tool lives in `tools/<name>.py` with two pieces:

```python
# Pure async implementation — no RunContext, trivial to unit-test
async def _impl(query: str) -> str:
    ...

# Wrapper that registers it with the agent and emits UI events
def register(agent: Agent[AgentDeps]) -> None:
    @agent.tool
    async def web_search(ctx: RunContext[AgentDeps], query: str) -> str:
        """Docstring becomes the LLM-visible tool description."""
        await ctx.deps.event_queue.put({"type": "tool_start", ...})
        result = await _impl(query)
        await ctx.deps.event_queue.put({"type": "tool_end", ...})
        return result
```

`agent/react.py::build_agent()` calls `register()` for each tool. To add a new
tool: create the module, add an import + `register()` call in `react.py`, done.

---

## Tests

**All tests are currently STUBS.** `uv run pytest` prints them as `SKIPPED`
with `STUB: implement real ...` messages — that's intentional, so the gap shows
up on every build until real tests are written. Files to fill in:

- `tests/unit/test_calculator.py` — happy paths, unsafe input rejection, math functions
- `tests/unit/test_python_repl.py` — exec, f-strings, import whitelist, deny list (`open`)
- `tests/unit/test_read_file.py` — JSON / CSV / plain text / errors
- `tests/integration/test_api.py` — FastAPI `TestClient` against `/api/health`, `/api/questions`, `/api/ask`
- `tests/integration/test_agent_flow.py` — use `pydantic_ai.models.test.TestModel` to run the agent deterministically and assert event flow

---

## Configuration Reference

| Env var | Default | Purpose |
|---|---|---|
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-6` | Any Bedrock inference-profile ID |
| `AWS_DEFAULT_REGION` / `AWS_REGION` | `us-east-1` | Bedrock region |
| `AWS_PROFILE` | — | Optional; boto3 picks it up |
| `APP_PORT` | `8000` | FastAPI port |
| `LOG_LEVEL` | `info` | uvicorn log level |

List available models: `aws bedrock list-inference-profiles --region us-east-1`.

---

## Roadmap (what this tutorial will grow into)

This is a living training repo. Planned additions:

- [ ] More tools for harder GAIA-style questions (URL fetcher, PDF parser, code sandbox via e2b)
- [ ] Config knobs: max tool calls per run, per-tool rate limits, allowed-imports list for REPL
- [ ] Multi-turn message history (conversation memory)
- [ ] Memory/RAG layer for improved step-by-step reasoning across sessions
- [ ] Real unit + integration tests (replace the stubs)
- [ ] CI workflow (ruff + mypy + pytest on push)

---

## Troubleshooting

**`ProfileNotFound: The config profile (Default) could not be found`**
→ AWS profile names are case-sensitive; use `default` (lowercase) or match your `~/.aws/credentials` section exactly.

**`ValidationException: The provided model identifier is invalid`**
→ The model / inference profile isn't available in your account. Run
`aws bedrock list-inference-profiles --region <region>` to see what's enabled.

**`AccessDeniedException`**
→ Request model access at AWS Console → Bedrock → Model Access.

**Frontend shows "Agent timed out after 120 seconds"**
→ Tune the timeout in `src/research_assistant/agent/runner.py`.

---

## Resources

- [Pydantic AI docs](https://ai.pydantic.dev/)
- [ReACT paper (Yao et al. 2022)](https://arxiv.org/abs/2210.03629)
- [GAIA benchmark](https://huggingface.co/datasets/gaia-benchmark/GAIA)
- [AWS Bedrock model IDs](https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html)
- [uv docs](https://docs.astral.sh/uv/)
