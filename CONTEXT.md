# Shared context — read this before writing any code

This is the canonical, **final** implementation contract for the remaining build window. It
supersedes every prior version of this file. Scope grew fast over the last hour (CopilotKit, Exa,
Ambiguous, Auth0, LiteLLM, Jinja, Pydantic, a 9-case eval suite all got added as "required" in
quick succession) — most of that was cut back down to fit the time actually remaining. If you read
an older copy of this file, or heard about the CopilotKit/Ambiguous/Auth0 requirements from
somewhere else: **this version is the one to follow.**

If you need to change a function signature or return shape from what's written here, say so in the
team channel before doing it — the other two devs are building against these exact shapes.

---

## 0. TL;DR architecture

One agent, many tools, run as a **fixed Python-orchestrated sequence** — not a model-directed
tool loop. The model is called at most twice per incident (diagnose, critique). Python code decides
which tool runs next, in what order, every time. This is more reliable and more demo-able than
letting the model choose its own tool calls, and it's what makes the 8-tool-call budget and
3-model-call budget actually enforceable instead of aspirational.

```
1. get_recent_logs(job_id)                    [Dev A]         — real evidence
2. get_schema(affected_table)                 [Dev A]         — real evidence
3. search_past_incidents(error_type)          [Dev C]         — memory lookup
4. search_repair_docs(query)                  [Dev B, Exa]    — optional, skip on failure
5. MODEL CALL 1 — diagnose                    [Dev B]         → {diagnosis, proposed_fix, confidence}
6. MODEL CALL 2 — critique_own_fix            [Dev B]         → {agrees, counter_argument, revised_confidence}
7. request_approval(diagnosis, fix, conf)     [Dev C]         — BLOCKING, human decides
      └─ rejected → stop, outcome = needs_human, never auto-retry
8. apply_fix(fix)                             [Dev A]         — bound to incident_id + fix_hash
9. rerun_pipeline(job_id)                     [Dev A]
10. log_incident(incident)                    [Dev C]         — JSON memory, authoritative
11. report_export(incident)   [optional, Dev C, Ambiguous]    — post-terminal, one attempt, doesn't
                                                                 count toward the 8-call budget
```

Terminal states: `fixed` | `needs_human` | `gave_up`. Nothing else. Steps 1–3, 5–10 are the
required path (7 tool calls + 2 model calls). Step 4 is optional evidence. Step 11 is optional
and entirely separable from whether the incident resolved.

**Demo failure scenario: `schema_drift`.** It's the most exercised scenario so far, and the seeded
memory already contains a past incident (`inc_004`) where a `retry_policy` fix was tried and
reverted — real ammunition for the critique step to reason about ("we tried something like this
before and it didn't work"). `null_spike` and `timeout` stay supported in code but get no further
demo polish. If someone has a strong reason to switch, raise it immediately — this choice is now
load-bearing for the Exa query, the Ambiguous report content, and the rehearsal plan.

---

## 1. Why one agent, many tools

See README.md. There is ONE agent loop (owned by Dev B). Dev A and Dev C do not write any agent
reasoning — they write plain Python functions the orchestrator calls as tools. Your functions
should have zero knowledge of the LLM, the loop, or each other. Pure input → output.

Clarification: mutation and approval tools necessarily have side effects. "Pure input → output"
means a JSON boundary and no embedded agent reasoning, not mathematical purity.

Do not add multi-agent delegation, model routing, semantic response caching, or new protocols
merely to make the architecture look advanced. This applies to vendor integrations too, not just
the agent design — see §4 for how that principle was actually applied to this build's scope.

---

## 2. Tool contracts (unchanged, already built, do not break these)

All tools take and return plain JSON-serializable Python dicts. No custom classes across the
boundary — this is what lets three people build in parallel without import hell.

### Owned by Dev A (`pipeline/`) — ✅ already implemented

```python
def get_recent_logs(job_id: str) -> dict:
    """
    Returns:
    {
      "job_id": str,
      "status": "failed" | "success",
      "error_type": str,          # e.g. "schema_drift", "null_spike", "timeout"
      "error_message": str,
      "timestamp": str,           # ISO 8601
      "affected_table": str,
      "row_count": int,
      "expected_row_count": int
    }
    """

def get_schema(table_name: str) -> dict:
    """
    Returns:
    {
      "table_name": str,
      "columns": [{"name": str, "type": str, "nullable": bool}],
      "last_changed": str          # ISO 8601, or null if unknown
    }
    """

def apply_fix(fix: dict) -> dict:
    """
    fix shape (produced by the agent, not by Dev A):
    {
      "fix_type": "schema_patch" | "config_change" | "retry_policy",
      "target": str,               # table or config name
      "change": dict                # free-form, specific to fix_type
    }
    Returns:
    { "applied": bool, "message": str }
    """

def rerun_pipeline(job_id: str) -> dict:
    """
    Returns:
    { "job_id": str, "status": "success" | "failed", "row_count": int }
    """
```

`change` remains a dict at the boundary, but Dev A allowlists supported keys/values/targets per
`fix_type` internally. Never execute arbitrary SQL or shell text supplied by the model. Never make
a pipeline pass by silently dropping records, disabling required-field validation, or increasing
timeouts without evidence the new limit is appropriate.

### Owned by Dev B (`agent/`) — orchestrator + 2 model-only reasoning steps

No tools. Owns the fixed-sequence orchestrator (§0) plus two model calls (not real "tool calls",
just prompted reasoning):

- **diagnose**: given logs + schema + past incidents (+ Exa sources if available), produce
  `{diagnosis, proposed_fix, confidence}`
- **critique_own_fix**: given the above, produce
  `{agrees: bool, counter_argument: str, revised_confidence: float}`

Basic shape validation (dict has the right keys, confidence is a float in [0, 1], fix_type is one
of the three allowed values) is enough. Do not build a Pydantic model layer for this — see §5 for
why that got cut.

### Owned by Dev C (`memory_approval/`) — ✅ already implemented

```python
def search_past_incidents(error_type: str) -> dict:
    """
    Returns:
    {
      "matches": [
        {
          "incident_id": str,
          "error_type": str,
          "root_cause": str,
          "fix_applied": dict,
          "outcome": "resolved" | "reverted" | "recurred"
        }
      ]
    }
    """

def request_approval(diagnosis: str, proposed_fix: dict, confidence: float) -> dict:
    """
    BLOCKING call — pauses until a human clicks approve/reject.
    Returns:
    { "approved": bool, "human_note": str }
    """

def log_incident(incident: dict) -> dict:
    """
    incident shape:
    {
      "incident_id": str, "error_type": str, "root_cause": str,
      "fix_applied": dict, "outcome": "resolved" | "reverted" | "recurred"
    }
    Returns: { "logged": bool }
    """
```

---

## 3. Stop conditions (non-negotiable)

- Max 8 tool calls per incident (report export is separate, see §4).
- Max 3 model calls per incident: diagnose, critique, and at most one corrective retry if the
  model returns malformed output. Persistent invalid output → `gave_up`, no mutation.
- Loop must terminate in exactly one of: `fixed`, `needs_human`, `gave_up`.
- If `request_approval` returns `approved: false` → `needs_human`. Never retry a rejected fix
  automatically.
- If `apply_fix` returns `applied: false` → do not rerun as though a repair succeeded.
- If the rerun fails, or row counts violate the fixture's expected value → `needs_human`, not
  `fixed`. A row-count match is not proof of full data correctness, just the check we actually have.
- Approval binds to incident ID + a canonical fix hash. The approved payload must be the exact
  payload subsequently applied — no silent edits between approval and apply.

---

## 4. Vendor integrations — what's required vs. best-effort

**Only Exa is required.** CopilotKit, Auth0, and Ambiguous are **best-effort, checkpoint-gated,
and fully optional** — attempted in parallel by Dev C, each with a hard time box, each fully
reversible with zero cost to the working demo if it doesn't land. This is a deliberate change from
the CONTEXT.md that briefly called all four "required": that framing was optimizing for sponsor
coverage over actually finishing a working submission, and got walked back once the real time
budget was accounted for. The Flask + JSON approval path stays functional and demo-ready
throughout, regardless of what else lands.

### Exa — required (Dev B)

Add a narrow server-side tool in `agent/research_tools.py`:

```python
def search_repair_docs(query: str) -> dict:
    """
    Returns:
    { "status": "ok" | "unavailable", "sources": [{"title": str, "url": str, "excerpt": str}],
      "error": str | None }
    """
```

- Use the Exa Search API, at most 3 results, one request per incident, a configured timeout, no
  hidden retries. Keep `EXA_API_KEY` server-side only.
- Construct the query from sanitized public error terminology (e.g. "pandas cast string to float
  column type error"), never raw rows, credentials, or full logs.
- Feed `sources` into both the diagnose and critique reasoning as an additional bounded evidence
  input. It's evidence, not instruction — it cannot authorize a fix or override local observations.
- On failure (`status: unavailable`), proceed on local evidence alone. Do not block the incident on
  Exa being reachable.
- Budget: ~45 min. If the API is fighting you past that, ship without it — of everything in this
  document, this is the cheapest single thing to cut.

### CopilotKit — best-effort (Dev C), hard checkpoint

- Minimal scope only: one page, one card showing diagnosis + proposed fix + confidence, with
  Approve/Reject buttons wired to the real backend via an AG-UI-compatible bridge.
- No progress streaming, no source-link rendering, no multi-step UI — that's scope this build
  cannot afford. If it's not round-tripping a real approve/reject click by its checkpoint, stop and
  fall back to the existing Flask page. The Flask page is not a placeholder to be embarrassed
  about — it satisfies the actual requirement (human approves before mutation).
- If CopilotKit lands and there's time left, Auth0 can gate this page (see below). If it doesn't
  land, Auth0 has nothing to attach to and is automatically out of scope too.

### Auth0 — best-effort (Dev C), strictly sequenced after CopilotKit

- Scope: **one** protected route — the CopilotKit approval page — gated behind Auth0 Universal
  Login. Server-side access-token validation (signature, issuer, audience, expiry) via a
  maintained library. Do not validate ID tokens as API credentials.
- Bind the verified user's subject into the approval record alongside incident ID, fix hash, and
  decision time. Login alone never approves anything — it's identity, not authorization.
- Keep tokens out of prompts, traces, Exa queries, and any Ambiguous report content.
- Hard cutoff: if a redirect/config error isn't resolved within the time-boxed window, abandon.
  OAuth setup problems have no partial-credit demo value — a half-configured login screen is worse
  to show than no login screen at all.

### Ambiguous — best-effort (Dev C), verify-before-build

- **Before writing any integration code**, fire one real request at the documented endpoint
  (`https://www.ambiguous.ai/agents/api`) and confirm what actually comes back. The earlier
  contract for this integration was written without confirming the schema — `llms.txt` was not
  retrievable during that review — so nothing here should be trusted until it's been hit live.
- If it works as documented: implement `memory_approval/report_export.py`, post one redacted
  incident report after the run terminates (incident ID, diagnosis, Exa sources if any, approval
  decision, observed outcome), persist the returned doc ID against the incident ID, and surface the
  link in the UI if CopilotKit is up (or just print it if not).
- If the schema doesn't match the docs, or auth fails, or it 404s: drop it. Don't spend the
  remaining budget reverse-engineering an undocumented API.
- Never expose Ambiguous mail/chat/other workspace operations to the model — export only, one
  deterministic call, not a model-selected tool. Doesn't count toward the 8-tool budget, gets at
  most one attempt with a timeout.

---

## 5. What got explicitly cut, and why

These were briefly "required" in an earlier version of this file. They're cut for the remaining
build window because they cost real hours and buy nothing a judge or a 2-minute demo video will
notice:

- **LiteLLM provider-abstraction wrapper** (`agent/model_client.py`). Direct SDK calls
  (Anthropic, optionally OpenAI if you want to burn those credits) are fine. Swapping providers is
  a 15-minute change either way; a wrapper layer for it is pure ceremony right now.
- **Jinja-templated prompts** (`agent/prompts/*.jinja`, `template_manager.py`). Keep prompts as
  plain strings in `agent/prompts.py`, as already built. Versioned template files with
  `StrictUndefined` are good practice for a real product, not for the next few hours.
- **Pydantic response validation.** Basic dict/key/range checks are enough (see §2). A full
  validation layer is invisible in a demo and costly to build correctly under time pressure.
- **The 9-case formal eval suite** (`evals/cases`, `evals/graders`, `evals/run.py`). If there's
  spare time at the very end, 2–3 lightweight sanity checks covering the demo path, a rejection,
  and a malformed-model-output case are enough. Nobody is running your eval harness in the next
  few hours — polish the actual demo instead.
- **Token/execution budgets beyond call counts.** Max-tool-calls and max-model-calls (§3) are
  the enforcement that matters. A separate token-budget/deadline system is unnecessary scope.

If the hackathon submission process specifically rewards evidence of these (e.g. a judge reads
CONTEXT.md itself), that's fine — the design intent is documented right here. Documented-but-not-built
costs zero implementation hours and still shows the thinking.

---

## 6. Current contract gaps — do not invent capabilities

- **Rollback:** no rollback tool exists. Don't claim automatic rollback or record `reverted`
  unless an actual reversal is independently confirmed. A failed repair needs human recovery.
- **Data-quality verification:** rerun output lacks null rates, rejected-row counts, checksums,
  samples. Demonstrate known fixtures and report only the narrower checks actually performed.
- **Schema history:** `get_schema` returns one current schema, not an upstream/downstream diff.
  Diagnosis must use evidence available in logs; insufficient evidence warrants escalation.
- **Incident outcomes:** `resolved | reverted | recurred` can't describe rejection, an unverified
  repair, or a tool failure. Keep those in an internal run trace, don't force them into memory.
- **Error naming:** `schema_drift` is a scenario name; logs may say `schema_mismatch`. Don't
  independently rename wire values — agree on any alias mapping before changing it.
- **Replay safety:** no idempotency key on public write signatures. Track incident ID + fix hash +
  mutation state internally; don't blindly replay writes after a crash.
- **`null_spike`/`timeout` rerun verification:** `rerun_pipeline` only re-checks the real failure
  condition for `schema_drift` (it re-reads `SCHEMA`). For the other two scenarios, `reset()`
  unconditionally clears status to success, so `rerun_pipeline` reports `fixed` regardless of
  whether `apply_fix` did anything real. Deliberately left this way — consistent with the decision
  elsewhere in this doc that only `schema_drift` gets demo polish. Do not demo `null_spike` or
  `timeout` as proof the fix loop verifies anything; they only prove the tool contracts round-trip.

---

## 7. Original execution plan (T+0:00 → T+5:00) — superseded, see §9

This was the 3-dev plan for the original 5-hour build window. Left in place as the historical
record of what was actually executed — most of it landed (pipeline, orchestrator, memory/approval
core, Exa, Auth0, Ambiguous, CopilotKit code all exist now). **For the current team size and time
remaining, follow §9, not the table below.**

Shared rules:
- Sync every 60 minutes, 5 minutes max: what's done, what's at risk, what's blocking someone else.
- The Flask + JSON + direct-API core is never modified by vendor work — it's the permanent fallback.
- **T+4:00 is a hard feature freeze.** Whatever's working at that point is what ships. The final
  hour is demo rehearsal and video recording only.

### Dev A — pipeline & demo reliability

| Time | Task | Done when | If behind |
|---|---|---|---|
| T+0:00–0:45 | Harden `schema_drift`. Run it 5×, confirm identical output every time | 5/5 identical runs against real (non-stub) tools | Drop further polish on `null_spike`/`timeout` entirely |
| T+0:45–1:30 | Confirm `apply_fix` really reverts the injected failure and `rerun_pipeline` reflects it | Rerun shows correct row counts/schema post-fix, not a hardcoded success | Flag as P0 in sync immediately — this blocks everything |
| T+1:30–4:00 | Float to whoever's behind (likely Dev C) | — | — |
| T+4:00–5:00 | Demo rehearsal + recording | 2 clean full run-throughs recorded | — |

### Dev B — orchestrator, model, Exa (critical path)

| Time | Task | Done when | If behind |
|---|---|---|---|
| T+0:00–1:00 | Rewrite orchestrator to the fixed sequence in §0 — Python orders every call, model invoked exactly twice | Full loop runs end-to-end against real tools, correct terminal state | Everyone pauses to help — this is the one task that can't be dropped |
| T+1:00–1:30 | Optional OpenAI swap | Same output shape, still works | Revert if flaky, don't spend more than 30 min |
| T+1:30–2:15 | Exa tool (`search_repair_docs`) | Real call returns sources, visibly used in diagnose/critique | Ship without it past 45 min in |
| T+2:15–4:00 | Support integration of whatever Dev C lands | Loop still resolves correctly with new pieces wired in | — |
| T+4:00–5:00 | Demo rehearsal support | — | — |

### Dev C — vendor integrations (highest variance, everything here is best-effort)

| Time | Task | Done when | Hard stop |
|---|---|---|---|
| T+0:00–0:20 | Fire one real request at the Ambiguous API, see what comes back | You know the real shape, or you know it's broken | 20 min — if it doesn't work, Ambiguous is dead, don't return to it |
| T+0:20–1:40 | CopilotKit minimal approval page | Real approve/reject click round-trips to the backend | **1:40 hard stop.** Not round-tripping → kill it, tell the team, Flask is the approval UI, full stop |
| T+1:40–2:40 | If CopilotKit landed: Auth0 gate on that one page. If not: `report_export.py` against the confirmed Ambiguous schema | One protected route works / one incident posts and returns a doc ID | **2:40 hard stop** either way |
| T+2:40–4:00 | Polish whatever's viable; otherwise help Dev A rehearse | — | — |
| T+4:00–5:00 | Support video/screenshots of whatever vendor pieces landed | — | — |

### Pre-submission checklist (T+4:45)

- [ ] `schema_drift` runs cleanly, 100% of rehearsal attempts
- [ ] Fixed-sequence orchestrator + exactly 2 model calls confirmed working
- [ ] Exa call visible in a run (or explicitly cut and mentioned as future work in the pitch)
- [ ] Approval step works — CopilotKit if it landed, Flask if it didn't, either is a legitimate demo
- [ ] Auth0 / Ambiguous: whatever's real gets shown live; whatever isn't gets one sentence in the
      pitch, never faked as working on camera
- [ ] Video recorded, under the time limit, shows the actual working path

---

## 8. Team workflow

Each developer works in a separate clone/branch, owns their directory, and coordinates on shared
schemas or fixture changes before making them. Merge small increments and exercise one end-to-end
fixture regularly. Preserve the public contracts in §2 unless all affected developers agree to an
update in the team channel first.

---

## 9. Amendment — 4 devs, T-4:00 to submission (supersedes §7)

Team grew from 3 to 4 partway through the build. This section is the current source of truth for
remaining work and ownership; §7 stays only as a record of what already happened.

### Verified status as of this amendment

- ✅ **Pipeline** (`pipeline/`) — real, fixture-backed (`pipeline/fixtures/orders.csv`), hardened,
  `python -m pipeline.smoke_test` passes (12 checks, no API key).
- ✅ **Agent orchestrator** (`agent/workflow.py`) — correctly implements the fixed sequence from §0:
  model called at most 3× (diagnose, critique, one correction retry), approval bound to a fix
  hash, rerun checked against expected row count, terminal states exactly `fixed`/`needs_human`/
  `gave_up`. 57 tests + 20 subtests pass (`agent/tests/`, `tests/`).
- ✅ **Memory + approval core** (`memory_approval/`) — real: `search_past_incidents`,
  `log_incident`, Flask approval page (blocking poll, timeout-safe, defaults to reject on timeout).
- ✅ **Exa** (`agent/research_tools.py`) — code correct per §4 (bounded, optional, degrades
  gracefully) but **not yet live**: no `EXA_API_KEY` in the repo-root `.env` that
  `agent/settings.py` actually loads.
- ✅ **Auth0** (`memory_approval/auth.py`) — real JWKS token verification implemented, not yet
  configured (`AUTH0_DOMAIN`/`AUTH0_AUDIENCE` unset).
- ✅ **Ambiguous** (`memory_approval/report_export.py`) — real, redacted export implemented, not
  yet configured (`AMBIGUOUS_API_KEY` unset) or fired against the live endpoint.
- ⚠️ **CopilotKit + AG-UI frontend** (`frontend/`, `agent/copilotkit/`) — full React + Auth0 +
  AG-UI wiring exists. Not yet verified to actually round-trip a real approve/reject click against
  `python -m agent serve`.
- ❌ **`main.py` (repo root) is broken.** It imports `agent.agent_loop` and `agent.tool_registry`,
  both deleted when the orchestrator was rewritten to `agent/workflow.py`. The real entrypoint now
  is `python -m agent run --inject-failure schema_drift --approval console` (or `--approval
  browser` for Dev C's Flask page) and `python -m agent serve` for the full server. Fix or delete
  `main.py` before anyone rehearses with the old command.
- ℹ️ A real Exa key was briefly committed in `agent/.env` and pushed to this (public) repo. It has
  since been rotated — the copy still in git history is dead and not urgent to scrub, but don't
  reuse that value.

### New decision: add OpenRouter as a model provider

We have $5 of real OpenRouter credit. `agent/model_client.py` already runs on LiteLLM, which
supports OpenRouter natively via a `openrouter/<provider>/<model>` model string and an
`OPENROUTER_API_KEY` env var — but `agent/settings.py`'s credential map only recognizes
`anthropic`/`openai` prefixes today, so `LLM_MODEL=openrouter/...` currently fails fast with
"Set LLM_MODEL to anthropic/... or openai/... and configure its provider key".

Plan: add `"openrouter": "OPENROUTER_API_KEY"` to the credential map in both
`LiveModel.__init__` and `integration_status()`. Use a cheap OpenRouter model as the default for
diagnose/critique; keep `ANTHROPIC_API_KEY` configured as a fallback/escalation path. No change to
the correction-retry logic in `workflow.py` for now — `MAX_MODEL_CALLS=3` already bounds worst-case
spend regardless of which provider is behind `LLM_MODEL`. Not yet implemented as of this amendment.

Note for the record (not urgent to fix): `agent/model_client.py` runs on LiteLLM, which §5 above
explicitly cut ("Direct SDK calls are fine... a wrapper layer is pure ceremony"). It works and is
tested, so leave it — just don't cite §5 as describing the current model_client.py.

### Remaining work, split 4 ways

| Owner | Task | Done when |
|---|---|---|
| **Dev A** | Fix or delete `main.py` (point at `python -m agent run`/`serve`); own final integration verification and rehearsal | `main.py` no longer crashes or is gone; 2 clean full run-throughs recorded |
| **Dev B** | Wire OpenRouter support (`agent/settings.py`, `agent/model_client.py`), populate root `.env` with a live Exa key + model key, run the first real end-to-end incident | `python -m agent run --inject-failure schema_drift --approval console` returns `fixed` using real model + Exa calls |
| **Dev C** | CopilotKit ↔ Auth0, in that sequenced order (per §4's hard stops) | Real approve/reject click round-trips through `agent/copilotkit` + `frontend/` against `python -m agent serve`; Auth0 gates that one route if CopilotKit lands |
| **Dev D** | Ambiguous: fire one real request first (per §4's "verify-before-build"), confirm or kill it; then float to whichever of B/C is behind | You know the real Ambiguous response shape, or it's confirmed dead and dropped |

Feature freeze and pre-submission checklist from §7 still apply — nothing here changes what
"done" looks like for the demo, only who's doing what and how much time is left to do it.

---

## 10. Amendment — procedural graph (self-evolving procedures)

Incident memory (`incidents.json`) is episodic: it records what happened, and every new incident makes
the model re-derive what to do from raw history. §6 also notes that its outcome vocabulary
(`resolved | reverted | recurred`) cannot describe a rejection or an unverified repair — so the most
valuable negative signal we get, an on-call engineer clicking Reject with a note, was logged nowhere
the agent could act on. The procedural graph closes that gap. No §2 signature changes.

**What it is.** `memory_approval/procedural_graph_seed.json` (tracked, immutable, consistent with
`incidents_seed.json`: `inc_001` validates `schema_patch`, `inc_004` prunes `retry_policy`) seeds a
runtime copy at `runtime/procedural_graph.json` (gitignored, honours `DEV_C_DATA_DIR`, written through
the same locked atomic transaction memory uses). Nodes are the fixed-sequence stages plus the three
fix types. Edges carry a `relation`, an `error_type` condition, `guidance`, `pitfalls`, and `evidence`
counters (`successes`, `rejections`, `failures`, `refusals`, `incidents`):

- `LEADS_TO` — procedural guidance between stages, e.g. `schema_patch -> rerun` says what verification
  must prove.
- `ADMISSIBLE` / `PRUNED` — `diagnose -> <fix_type>` for an error type. **Derived, never asserted:** an
  edge is PRUNED exactly when `rejections + failures >= policy.prune_after_negatives` and that sum
  outweighs `successes`. `validate_graph` rejects any file that says otherwise, so the graph cannot
  contradict its own evidence.

**Where it plugs in.** `run_incident(..., procedures=None)` accepts an optional object exposing
`localize(stage, error_type, proposal)` and `refine(result)`; `agent/live.py` passes
`ProceduralGraph()`. `None` is byte-identical to the previous behaviour, which is how the existing
tests still run unchanged.

1. Before each of the two model calls, the 2-hop neighbourhood of the active node is rendered into
   the prompt as `procedures` (JSON, under a header naming it evidence, not instructions; ~300
   tokens). It is not a tool call and consumes no budget.
2. After diagnose, if the proposed `fix_type` is PRUNED for this error type, Python stops with
   `needs_human` / `terminal_event: proposal_pruned` **before** the critique and before any approval
   request. This is §3's "never retry a rejected fix" enforced structurally across incidents; the
   guidance makes the model avoid the repair, the guard guarantees it. Costs one model call, not two.
3. After the terminal state, `refine(result)` runs — post-terminal like report export, outside the
   8-tool and 3-model budgets, and unable to change the outcome. It reads the run record (not the
   `log_incident` payload, whose shape and outcome vocabulary are unchanged) and applies fixed rules:

   | `terminal_event` | graph change |
   |---|---|
   | `verified` (outcome `fixed`) | `successes += 1`; the exact approved fix is kept as a validated example; edge created if new |
   | `rejected` (human said no) | `rejections += 1`; pitfall `"<incident>: rejected by human review — <note>"` |
   | `verification_failed` (applied, rerun did not pass) | `failures += 1`; pitfall with the rerun status |
   | `apply_failed` (tool refused the change) | pitfall with the tool's message; `refusals += 1` — informational, **not** negative evidence, because a refusal is about the exact change, not the fix type |
   | anything else (critique disagreed, budgets, invalid output, tampered approval, `proposal_pruned`) | none |

   Only humans and verified outcomes prune. Model-generated text never becomes a persistent pitfall.
   Notes and tool messages are sanitized (control characters, bearer/secret patterns) and capped.

**Commit gate ("safe offline evolution").** The candidate graph is built in memory under the file lock
and written only if it (a) passes `validate_graph`, (b) renders every localized view under the prompt
budget, and (c) `python -m pipeline.smoke_test` passes in a subprocess (policy
`run_smoke_test_before_commit`, on by default). Otherwise the file is untouched and
`procedures.refinement.reason` in the run result says why. Every commit bumps `revision` and appends a
bounded changelog entry (incident, event, changes). Refinement is idempotent per incident id, and a
corrupt runtime file is never overwritten (same rule as memory). Inspect or restore it with:

```bash
python -m memory_approval.procedural_graph show                     # admissible/pruned per error type, changelog
python -m memory_approval.procedural_graph explain --error-type schema_drift [--fix-type retry_policy]
python -m memory_approval.procedural_graph reset                    # back to the seed
```

**What it deliberately does not do.** No additional model call — the refiner is rules over human
decisions and verified outcomes, so §1 and §3 still hold and the graph evolves deterministically. No
change to the required tool sequence or the two reasoning calls: the "fast path" means the validated
fix arrives as concrete evidence and pruned repairs are blocked, not that evidence steps are skipped.
No git commit of the graph. No Slack: the rejection note is whatever `human_note` the approval channel
returned (console, Flask page, CopilotKit). Any graph failure degrades to a warning and the run
proceeds on evidence alone. Policy thresholds live in the graph's `policy` block, not in code.

**Additive result keys:** `error_type`, `terminal_event`, `application` (`apply_fix` confirmation),
`procedures` (`revision`, `fast_path`, `guard`, `refinement`), plus `kind: "procedure"` trace entries.

**Demo beat.** Incident 1: reject the proposal with a note → `[procedural_graph] refined to revision N:
pruned diagnose -> …`. Incident 2: the note is in the diagnose prompt, the model proposes the validated
repair, approve → `fixed` → `reinforced …`. `show` displays the changelog between the two. If the model
ever ignores the pitfall, the guard stops it before approval — that is the safety property, not a
failure of the demo.

---

## 11. Amendment — always-on watcher and free-model tiers

§0's sequence is unchanged, but it only ran when someone typed `python -m agent run` or clicked
*Investigate flagged batch*, and `agent/model_client.py` sent every call to one model. This amendment
makes the server watch its own ingestion table and route between free OpenRouter models. We have no
paid model access, so the tiers are about latency and rate limits, not dollars: every free model
shares one cap of 20 requests per minute and 50 per day (1000 once an account has bought $10 of
credit). Model IDs on the free roster rotate weekly, so nothing here hard-codes one.

**Watcher (`agent/watcher.py`, `python -m agent serve --watch`).** A thread inside the server, not a
second process: `_RUN_LOCK`, the runs table and the AG-UI queues are process-local. It wakes when
`POST /api/ingestion` flags a batch and every `AGENT_WATCH_INTERVAL_SECONDS` otherwise, and starts the
oldest unclaimed `schema_drift` batch through the same `investigate()` path the button uses, as the
batch's owner, so ownership, approval and the UI are untouched. Idle cost: zero model calls, one SQLite
query. Rules:

- One incident at a time (§2's serialization rule), FIFO by ingestion time.
- Retry only a run with `terminal_event = model_error`, `application = null`,
  `mutation_state = not_attempted` and a transient last provider error: at most two retries, 60 s and
  300 s later, each a fresh run ID (`ingestion.run_id` moves to the newest). A human decision, an
  applied repair, an exhausted correction allowance, or a configuration error (bad key, missing model)
  is final. "A restart never replays uncertain work" still holds: a run left `started` by a crash is
  never touched.
- `AGENT_DAILY_MODEL_CALL_BUDGET` (default 40, UTC day, table `model_budget`) is charged with every
  provider request of every run the server finishes — manual or watcher, successful or rate-limited —
  before the run becomes observable as finished. When fewer than `MAX_MODEL_CALLS` remain, the watcher
  reports `budget_exhausted` and starts nothing. CLI runs are outside the server and not counted.
- `GET /api/watcher` (read:incidents) exposes running state, budget, retry policy and the last tick.

**Tiers (`agent/model_client.py`).** `LLM_MODEL` is the heavy tier; `LLM_FAST_MODEL` (optional) the
fast tier; `LLM_FALLBACK_MODELS` a comma-separated tail appended to both (`fast, heavy, fallbacks…`
and `heavy, fallbacks…`). The workflow passes hints with every call — `stage`, `fast_path` (the
procedural graph's validated fast path exists for this error type), `correction` (this is the retry
after malformed output). The fast tier serves exactly `stage = diagnose ∧ fast_path ∧ ¬correction`;
critique, novel error types and corrections always use the heavy tier. That keeps the adversarial
step on the strongest configured model, and — since the graph guard (§10) blocks pruned repairs in
Python and a human still approves — the safety story never depended on which model diagnosed.

**Contract clarification (§2, model callable).** "At most one provider request per call" becomes
"one reasoning request per call": the client may retry a request that produced *no answer*
(`RateLimitError`, `ServiceUnavailableError`, `InternalServerError`, `APIConnectionError`,
`Timeout`, `NotFoundError`, empty reply) against the next model in the tier, at most three providers
per call, every attempt listed in `model_requests` with `tier`, `stage` and `status`
(`returned | fallback | error`). Malformed content is never a fallback trigger — that remains the
workflow's single correction and `MAX_MODEL_CALLS = 3` still bounds reasoning. `AuthenticationError`
surfaces immediately. Test fakes that take `(*, system, prompt)` must accept `**hints`.

**`approval_expired` (§3).** An approval nobody answers is not a rejection. Both adapters
(`agent/server.py`, `memory_approval/approval_server.py`) add `expired: true` to the decision when the
timeout fires — the key is absent on a real decision, so Dev C's return shape is unchanged — and the
workflow ends the run as `needs_human` with `terminal_event = approval_expired`. The procedural graph
learns nothing from it; without this, two unattended timeouts would have pruned a validated repair.

**Deliberately not done.** No Slack: the approval callable is already the extension point (a Slack
Block Kit card is another `request_approval` implementation plus a route for the button payload), but
there is no workspace, token or public URL to point it at, and the browser/CopilotKit decision path
already feeds `human_note` into the graph. No paid escalation tier. No change to the seven required
tool calls, the two reasoning calls or any §2 signature. No frontend change; the ingestion monitor
already shows a watcher-started run as *Open investigation*.

**Verify.** `python -m agent check` prints `model_tiers`; `python -m agent check --model` makes one
request on the heavy tier; `python -m agent serve --local-no-auth --watch`, then
`POST /api/ingestion` with a string `amount`, then `GET /api/watcher` and `GET /api/ingestion` — the
batch has a `run_id` without a click.
