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

## Shared verification dataset

`pipeline/fixtures/orders.csv` — 60 deterministic rows (fixed random seed) matching the `orders`
schema from CONTEXT.md §2 (`order_id`, `customer_id`, `amount`, `created_at`). `row_count` /
`expected_row_count` in every tool response now derive from this file's real length instead of a
hardcoded number, so anyone on the team can open the CSV and check the numbers `get_recent_logs`
reports actually make sense.

Use it as the common reference point when sanity-checking output, independent of who's testing:

```bash
wc -l pipeline/fixtures/orders.csv   # 61 (60 rows + header) — matches expected_row_count: 60
```

- `schema_drift` — fails all 60 rows (whole-column type failure), fix reverts to 60/60.
- `null_spike` — fixed 42% of the 60 rows treated as affected (currently a count derived from the
  fixture size, not per-row null injection — the CSV itself isn't mutated for this scenario).
- `timeout` — row_count 0, no fixture dependency.

If you regenerate the fixture, keep the same seed/row count convention (see the generation snippet
in the branch history) so numbers stay reproducible across everyone's machine.
