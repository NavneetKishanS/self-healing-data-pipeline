# Dev B — the agent loop

Branch: `dev-b/agent-loop`

## Your job in one sentence

Write the loop that calls tools, reasons over the results, and knows when to stop.

## Why you can start immediately

`agent/tool_registry.py` already wires in the *stub* versions of every tool from `pipeline/` and
`memory_approval/`. They return realistic fake data matching the exact shapes in CONTEXT.md. You
do not need to wait for Dev A or Dev C to write a single line of real code — swap `USE_STUBS = True`
to `False` in `agent/tool_registry.py` once their real implementations land, and nothing else in
your code should need to change if everyone respected the contract.

## Build order

1. `agent/prompts.py` — write the system prompt. It needs to instruct the model to: gather evidence
   with tools before diagnosing, always run the critique step before finalizing a fix, and never
   call `apply_fix` without an approved `request_approval` result first.
2. `agent/agent_loop.py` — the actual loop. Skeleton is provided with the tool-calling boilerplate
   for the model API — fill in the reasoning steps and stop-condition enforcement.
3. The critique step is the differentiator — do not skip it even under time pressure. It can be as
   simple as a second model call with a prompt like "argue against this diagnosis using the same
   evidence — what would make it wrong?"

## What "done" looks like

```bash
python -m agent run --inject-failure schema_drift --approval console
# streams: tool calls -> diagnosis -> critique -> approval request -> (waits) -> fix applied -> rerun -> logged
```

Note: this file predates the rewrite to `agent/workflow.py`'s fixed-sequence orchestrator (see
CONTEXT.md §0/§9) — `agent/agent_loop.py` no longer exists. The command above is current; the rest
of this file describes the superseded ReAct-loop approach.

## Do not

- Don't build separate agents for diagnosis/fix/critique. One system prompt, one loop, multiple
  reasoning steps inside it — see README.md for why.
- Don't skip the max-tool-calls guard. An infinite loop live on stage is the worst possible demo
  failure.
- Don't hardcode assumptions about Dev A's or Dev C's internals — only call them through the
  functions in `agent/tool_registry.py`.
