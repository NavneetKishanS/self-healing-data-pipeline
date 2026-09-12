# Dev A — synthetic pipeline + environment tools

Branch: `dev-a/pipeline-tools`

## Your job in one sentence

Build something that runs, that you can break on command, and give the agent four functions to
read and fix it.

## Why you go first

Everyone else's stub tools fake your real output shape. But the demo is only as good as your
pipeline is reliable — a flaky synthetic pipeline is the single most common way hackathon demos
fail. Budget real time on making failure injection deterministic, not on making the pipeline
realistic. Nobody cares if it's "just" three Python functions chained together as long as it fails
predictably and recovers visibly.

## Build order

1. `pipeline/synthetic_pipeline.py` — 3 chained steps (e.g. extract -> transform -> load) operating
   on a small in-memory or SQLite dataset. Each step should be able to succeed or fail based on a
   flag you can set programmatically.
2. `pipeline/failures.py` — one function per failure scenario in CONTEXT.md that mutates the
   pipeline's state to cause that specific, reproducible failure.
3. `pipeline/tools_pipeline.py` — implement the four contract functions from CONTEXT.md for real,
   reading actual state from your pipeline.

## What "done" looks like

```bash
python -m pipeline.synthetic_pipeline --inject-failure schema_drift
# prints a failure
python -c "from pipeline.tools_pipeline import get_recent_logs; print(get_recent_logs('job_1'))"
# prints the exact dict shape in CONTEXT.md, with error_type == 'schema_drift'
```

## Do not

- Don't reach for real Airflow/dbt unless someone already has it running locally from before —
  standing up real orchestration infra eats hours you don't have.
- Don't build more than 3 failure scenarios. One reliable one beats three flaky ones.
- Don't change the tool contract shapes without telling Dev B and Dev C first.
