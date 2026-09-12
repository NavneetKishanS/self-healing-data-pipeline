# Mend — a self-healing data pipeline agent

An agent that watches a data pipeline, detects schema drift as rows arrive, diagnoses the failure
with a language model, argues against its own conclusion, and then **stops and waits for a human**
to approve the exact change before anything is modified.

The interesting part is not that a model can propose a fix. It is that the loop is **bounded by
construction**: Python decides which tool runs next, the model never drives execution, every run
ends in one of three terminal states, and a repair a human rejected once is structurally blocked
from being proposed again.

---

## Quickstart

**Requirements:** Python 3.11+, Node 20+ (the frontend's build tooling requires it), and an
[OpenRouter](https://openrouter.ai) API key. Free models work — see [Model configuration](#model-configuration).

```bash
git clone https://github.com/NavneetKishanS/self-healing-data-pipeline.git
cd self-healing-data-pipeline

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # one file installs everything

cp .env.example .env                     # then add your OPENROUTER_API_KEY
python -m agent check --model            # verifies the key reaches a live model
```

### Try it without any API key

```bash
python -m agent.demo --decision approve
```

A narrated walkthrough of the real workflow against a three-row fixture with scripted model
responses. No network calls, nothing saved. Add `--show-prompts` to inspect the exact prompts.

### One incident, end to end, in the terminal

```bash
python -m agent run --inject-failure schema_drift --approval console
```

Injects a known failure, gathers evidence, runs diagnose and critique, then pauses for you to type
`approve`. Use `--approval browser` for the standalone Flask page, or `--approval reject` for a
non-interactive run that stops at the approval gate.

### The full console

Four processes. Each in its own terminal, from the repo root:

```bash
# 1. Agent backend — approvals, AG-UI stream
python -m agent serve --local-no-auth

# 2. Agent backend — ingestion monitor (same code, second port)
AGENT_PORT=8001 python -m agent serve --local-no-auth

# 3. CopilotKit runtime bridge
cd agent/copilotkit && npm ci --ignore-scripts && npm start

# 4. Frontend
cd frontend && npm install && npm run dev
```

Then open **http://127.0.0.1:5173**.

| Port | Process |
|---|---|
| 5173 | Frontend (Vite) |
| 8000 | Agent backend — approvals, AG-UI |
| 8001 | Agent backend — ingestion + transmitter |
| 4000 | CopilotKit runtime bridge |
| 5050 | Standalone Flask approval page (only for `--approval browser`) |

> `--local-no-auth` is loopback-only development mode. Never deploy it. Running without the flag
> requires Auth0 (see [Authentication](#authentication)).

---

## How it works

### The incident sequence

Eleven steps in a fixed order, enforced by Python. The model reasons; it does not choose what runs
next.

| # | Step | Kind |
|---|---|---|
| 1 | Ingestion validation — every batch type-checked on arrival | no model |
| 2 | `get_recent_logs` — job status, error type, row counts | tool |
| 3 | `get_schema` — current column types + repair contract | tool |
| 4 | `search_past_incidents` — what was tried before, what was reverted | tool |
| 5 | **Diagnose** — structured diagnosis, one proposed fix, confidence | model |
| 6 | **Critique own fix** — argues against step 5 on the same evidence | model |
| 7 | Procedural guard — halts if this fix type was pruned for this error | policy |
| 8 | `request_approval` — blocking, hash-bound | human |
| 9 | `apply_fix` — converts values on an isolated copy | tool |
| 10 | `rerun_pipeline` — re-validates row count *and* schema | tool |
| 11 | `log_incident` — writes the outcome back to memory | tool |

Detection (step 1) never waits on a model and costs no tokens. If the critique disagrees at step 6,
the run stops at `needs_human` before anything is touched.

### The guarantees

- **Bounded:** max 8 tool calls, 3 model calls per incident, counted in Python.
- **Three terminal states:** `fixed`, `needs_human`, `gave_up`. Nothing loops back.
- **No silent retries:** a rejected fix is never retried automatically. Malformed model output gets
  exactly one correction attempt, then stops.
- **Approval integrity:** the approved fix is hashed. Any edit between approval and apply stops the
  run and demands a fresh decision.
- **Verified, not assumed:** a passing status flag is not accepted as proof — row count and schema
  validity must both pass.

### The learning loop

Incident memory records *what happened*. A second store — the **procedural graph**
(`memory_approval/procedural_graph.py`) — records *what to do about it*. A verified repair
reinforces that fix type for that error type; a human rejection or a failed verification
accumulates negative evidence until the fix type is pruned and structurally blocked at step 7.

Pruning is **derived from the evidence counters, never asserted by the model**, and a rewritten
graph is only committed if it validates *and* `python -m pipeline.smoke_test` still passes.

```bash
python -m memory_approval.procedural_graph show
python -m memory_approval.procedural_graph explain --error-type schema_drift
```

### Always-on mode

```bash
python -m agent serve --local-no-auth --watch
```

A watcher thread (`agent/watcher.py`) idles at **zero model calls**, wakes when an ingested batch is
flagged, and starts the same investigation the *Investigate* button would. One incident at a time.
It retries only runs that ended before any model answered, and a daily model-call budget keeps it
inside the free tier's cap. Status at `GET /api/watcher`.

---

## Model configuration

Free OpenRouter models are the default target. Three variables, all optional except the key:

| Variable | Role |
|---|---|
| `LLM_MODEL` | Heavy tier — novel incidents and **every** critique |
| `LLM_FAST_MODEL` | Fast tier — a diagnose call that already has a validated fast path |
| `LLM_FALLBACK_MODELS` | Tried in order when a request never produces an answer |

Fallback happens **within a single call** on rate limits, outages, timeouts and empty replies —
malformed content never falls through, so the 3-model-call bound still holds. Leaving the tier
variables unset gives plain single-model behaviour.

> **Free model IDs rotate weekly.** Check <https://openrouter.ai/models?q=free> and verify with
> `python -m agent check --model`. Free accounts are capped at ~50 requests/day, which is what
> `AGENT_DAILY_MODEL_CALL_BUDGET` exists to respect.

Any OpenAI-compatible endpoint works via the `openai/` prefix — set `OPENAI_API_BASE`. Anthropic
works directly via `anthropic/`. See `.env.example` for every variable.

---

## Project layout

```
agent/               The agent: fixed-sequence workflow, model client, watcher,
                     AG-UI + REST server, Exa/Slack/Ambiguous adapters
  workflow.py        The 11-step sequence and all the bounds
  model_client.py    Tiered model routing with in-call fallback
  watcher.py         Always-on mode
  server.py          REST + AG-UI + ingestion endpoints
  dataset.py/iris.py Tool adapters over the fixtures
memory_approval/     Incident memory, human approval, procedural graph
pipeline/            Synthetic pipeline, failure injection, smoke test
frontend/            React console — ingestion monitor, approvals, how-it-works
pipeline-slack-bot/  Optional Slack approvals (Socket Mode)
CONTEXT.md           Tool contracts and design decisions, with rationale
docs/                Per-area detail
```

---

## Testing

```bash
pytest -q                        # full suite
python -m pipeline.smoke_test    # pipeline determinism + repair verification, no API key
cd frontend && npm run build     # frontend production build
cd agent/copilotkit && npm test  # runtime bridge
```

---

## Authentication

Local development uses `--local-no-auth` (loopback only). For a real deployment, configure
`AUTH0_DOMAIN` and `AUTH0_AUDIENCE`, run `python -m agent serve` without the flag, and grant the
`read:incidents`, `run:incidents` and `approve:fixes` permissions. The frontend needs the matching
`VITE_AUTH0_*` values. Access tokens are verified against the tenant JWKS — signature, issuer,
audience and expiry.

---

## What is real, and what is simulated

Stated plainly, so nothing here is mistaken for production readiness.

| | |
|---|---|
| **Model reasoning** | **Real.** Live provider calls. No scripted fallback is ever substituted for a live run. |
| **Drift detection** | **Real.** Values type-checked against the expected schema on arrival, in the backend. |
| **Repair** | **Real, but isolated.** Values are genuinely converted on a copy and re-validated. The original flagged record is preserved. |
| **Human approval** | **Real.** Blocking, hash-bound, expires if unanswered. |
| **The pipeline itself** | **Simulated.** A local fixture batch stands in for a warehouse. No production broker or sink is connected, so a passing rerun is not independent proof of a production repair. |

Known limits: single-process locking (not distributed), no rollback tool, and a restarted server
does not resume pending investigations. `CONTEXT.md` §6 tracks these explicitly.

---

## License

See `pipeline-slack-bot/LICENSE`. Dataset: Kaggle `uciml/iris` (CC0) — provenance in
`agent/fixtures/README.md`.
