# Dev C — memory + human approval

Branch: `dev-c/memory-approval`

## Your job in one sentence

Give the agent something to remember, and give a human a button to press before anything real
happens.

## Why this matters more than it sounds

The memory lookup is the project's actual differentiator (an agent that gets faster/better because
it's seen this failure before, vs. one that reasons from scratch every time). The approval step is
what keeps this "human in the loop" instead of "autonomous agent that could break something on
stage." Both are small to build. Don't over-invest in either — a JSON file and a plain HTML page
are the right amount of engineering here, not a graph database or a real Slack app.

## Build order

1. `memory_approval/incidents_seed.json` — write 4-5 fabricated past incidents matching the
   `search_past_incidents` return shape in CONTEXT.md. Make at least one of them match your
   demo's chosen failure scenario exactly, and give it `"outcome": "resolved"` — this is what
   makes the memory lookup visibly pay off in the demo.
2. `memory_approval/memory_store.py` — implement `search_past_incidents` (simple keyword match on
   `error_type` is enough, no embeddings needed for 5 records) and `log_incident` (append to the
   same JSON file).
3. `memory_approval/approval_server.py` — the simplest possible web page: show the diagnosis,
   proposed fix, and confidence score, with Approve/Reject buttons. A single Flask route with
   inline HTML is enough. `request_approval` blocks (polls or uses a simple event) until a button
   is clicked.

## What "done" looks like

```bash
python -c "from memory_approval.memory_store import search_past_incidents; print(search_past_incidents('schema_drift'))"
# returns at least one match from your seed file

python -m memory_approval.approval_server
# opens a page, shows a hardcoded test diagnosis, Approve/Reject work and print to console
```

## Do not

- Don't build real Slack OAuth unless someone on the team has done it before and is confident it's
  a 20-minute job. A browser page is a legitimate, honest substitute for the demo.
- Don't reach for a graph database. A JSON list with an `error_type` field you filter on is the
  right scope.
- Don't let `request_approval` hang forever with no fallback — add a "reject after N seconds" or a
  manual override so a stuck browser tab can't kill a live demo.
