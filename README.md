# Self-healing data pipeline (agentic)

One agent, a rich toolset, human-in-the-loop approval. Built for a 6-hour hackathon window, 3 developers.

## The architecture decision (say this to judges)

We deliberately chose **one agent with many tools** over a multi-agent swarm. The failure-diagnosis,
fix-proposal, and self-critique steps are related reasoning over the same evidence, not independent
jobs — so they live in one context window and one loop, not three services passing messages. This
follows the standard advice in the agent-architecture literature: reach for multiple agents only when
a single one is doing too many *unrelated* jobs, or you need real parallelism/isolation. We don't.

## The loop

```
failure detected
  -> get_recent_logs
  -> get_schema (if relevant)
  -> search_past_incidents   (JSON memory of prior failures + fixes)
  -> [model reasons to a diagnosis + proposed fix]
  -> critique_own_fix        (second reasoning pass, argues against the diagnosis)
  -> request_approval        (pauses, human approves/rejects in browser)
  -> apply_fix               (on approval)
  -> rerun_pipeline
  -> log_incident             (writes outcome back to memory, closes the loop)
```

Stop conditions: max 8 tool calls per incident. Must end in one of: `fixed`, `needs_human`, `gave_up`
(with reasoning attached). Never loop silently.

## The learning loop (procedural graph)

Memory alone is episodic. A second store, the procedural graph (`memory_approval/procedural_graph.py`,
seeded from `procedural_graph_seed.json`), turns outcomes into procedure: which fix types are admissible
or pruned per error type, why, and what the rerun must prove. It is rendered into both model prompts,
blocks a repair a human already rejected before any approval is requested, and rewrites itself offline
after each incident — committing only if the new graph validates and the pipeline smoke test passes.
No extra model call, no change to the tool sequence. Details and rules: CONTEXT.md §10.

```bash
python -m memory_approval.procedural_graph show                      # admissible / pruned repairs + changelog
python -m memory_approval.procedural_graph explain --error-type schema_drift
```

## Repo layout

```
pipeline/           Dev A — synthetic pipeline + environment tools (read/write the world)
agent/              Dev B — the agent loop, prompts, tool-calling, stop conditions. Run with
                    `python -m agent run` / `python -m agent serve` — the integration point.
memory_approval/    Dev C — incident memory (JSON) + human approval UI
CONTEXT.md          Shared tool contracts. Read this before writing any tool. Do not change a
                    signature without telling the other two devs.
docs/               Per-dev detailed context (read your own file first)
```

## Branch strategy

```
main                — this boilerplate, protected, only integration merges land here
dev-a/pipeline-tools
dev-b/agent-loop
dev-c/memory-approval
```

Each dev builds on their branch against the **stub** implementations already in the other two
folders (see below — every tool has a working fake version that returns plausible fake data). This
means nobody blocks on anybody for the first ~2 hours.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your model API key
python -m agent run --inject-failure schema_drift --approval console   # one full incident end to end
```

## Timeline (6 hours, 3 devs)

| Time | Dev A | Dev B | Dev C |
|---|---|---|---|
| 0:00–1:30 | Build real synthetic pipeline + failure injection | Write system prompt + loop against stub tools | Build JSON memory store + seed data, approval page skeleton |
| 1:30–3:00 | Real `get_recent_logs`/`get_schema` online | Swap stubs for Dev A's real tools | Real `search_past_incidents` + `log_incident` |
| 3:00–4:00 | Real `apply_fix`/`rerun_pipeline` online | Integrate Dev C's memory + approval tools, first full loop | Real approval page wired to loop's pause/resume |
| 4:00–5:00 | All three: run all failure scenarios end to end, fix what breaks | | |
| 5:00–6:00 | All three: polish demo narrative, rehearse, do not add features | | |

## Demo script (write this down now, follow it later)

1. Show the pipeline running clean.
2. Inject one failure live (`--inject-failure schema_drift`).
3. Show the agent's tool calls streaming (logs -> schema -> memory search).
4. Show the critique step explicitly arguing against the first-pass diagnosis.
5. Show the approval page, click approve.
6. Show the pipeline going green again.
7. Show the memory file with the new incident appended — "next time this happens, it's faster."

Rehearse this exact sequence twice before presenting. Pick the one failure scenario that's most
reliable, not the most impressive-sounding one.
