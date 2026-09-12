# Agent foundation — integration boundary

`workflow.py` follows the fixed sequence in `CONTEXT.md`. Jinja templates are used per the user's
latest decision, overriding the context's older statement that templating was cut. Nothing here
imports `pipeline/`, `memory_approval/`, a vendor SDK, or `main.py`.

## Calling it

For a narrated, interactive example with no API keys, run from the repository root:

```sh
.venv/bin/python -B -m agent.demo
```

This uses the real workflow with a three-order in-memory fixture and scripted model responses.
Type `approve` at the prompt to repair the fixture, or reject to leave it untouched. Nothing is
saved. Use `--decision approve` or `--decision reject` for a noninteractive run, and
`--show-prompts` to inspect the actual Jinja-rendered requests. No live model or Exa calls occur.

```python
from agent import run_incident

result = run_incident(
    "job_1",
    incident_id="your-stable-incident-id",
    tools={
        "get_recent_logs": get_recent_logs,
        "get_schema": get_schema,
        "search_past_incidents": search_past_incidents,
        "search_repair_docs": search_repair_docs,  # optional
        "request_approval": request_approval,
        "apply_fix": apply_fix,
        "rerun_pipeline": rerun_pipeline,
        "log_incident": log_incident,
    },
    model=call_model,
)
```

The functions in this example are supplied by the integration caller. Tool argument names and
return shapes are those in `CONTEXT.md`; the orchestrator calls them with keyword arguments.
There is no global stub switch. Tests and real runs use the same workflow with different functions.

`call_model(*, system: str, prompt: str) -> str` returns JSON text, making at most one provider
request per invocation. The provider adapter owns transport timeouts, credentials, and disabling
hidden retries. The workflow owns the two reasoning stages and one shared format-correction retry.
The foundation supplies this boundary; live model and Exa adapters are the next Dev B increment.

## Responsibilities

- Dev A: implement the four pipeline functions, validate actual supported mutations, and make
  reruns evaluate post-fix state. This workflow never resets the pipeline. It checks matching job,
  successful status, and exact expected row count for the current full-refresh fixtures.
- Dev B: maintain workflow order, prompts, model output checks, research input, and terminal states.
  `prompts.render(name, **context)` manages the three repository-owned Jinja text templates;
  missing variables fail explicitly, and tool evidence is JSON data rather than template source.
- Dev C: provide memory and a blocking approval function. Bind browser decisions to the caller's
  incident ID and displayed proposal, expire stale decisions, and return a real boolean. The
  workflow hashes and privately snapshots the fix, detects edits during approval, and records
  the decision, but cannot authenticate a browser or identify stale clicks from a bare boolean.

All tools must enforce their own I/O timeouts. Serialize runs that share mutable pipeline or
approval state. This foundation does not provide durable replay protection, distributed locks,
or cancellation of a synchronous tool already executing. Do not automatically restart a run
after an uncertain write. No model receives permission to execute tools itself.

## Results and verification

Results are JSON-serializable dictionaries containing `incident_id`, `job_id`, `outcome`, `reason`,
`tool_calls`, `model_calls`, `diagnosis`, `critique`, `approval`, `verification`, `memory_logged`,
`mutation_state`, `sources`, `warnings`, `prompt_versions`, and an ordered `trace`.
`diagnosis` contains the complete diagnosis/proposal/confidence object. `trace` contains stage names,
statuses, and elapsed seconds, not raw evidence. Model-produced diagnosis and notes can still
contain sensitive data: the UI/report integration must redact before exporting them.

`fixed` means the rerun checks passed. If memory persistence fails afterward, `memory_logged` is
false and a warning is returned; the repair is not replayed. Other operational failures return
`needs_human` or `gave_up` with a reason. Missing dependencies or invalid caller configuration
raise `ValueError` before any execution. A healthy job returns `needs_human` without mutation,
because this entry point handles failed incidents rather than routine successful runs.

Optional report delivery belongs to the caller after the terminal result and is outside the
eight-tool repair budget. The result is an internal run record, not the fixed `log_incident` payload.

Run the offline tests from the repository root (Jinja is already in `requirements.txt`):

```sh
.venv/bin/python -B -m unittest discover -s agent/tests -v
```

These tests use a stateful fixture and scripted model responses. They establish orchestration
behavior, not live model quality or correctness of another developer's pipeline implementation.
