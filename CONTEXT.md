# Shared context — read this before writing any tool

Everything below is a **contract**. If you need to change a function signature or return shape,
say so in the team channel before doing it — the other two devs are building against these exact
shapes right now, using stub implementations that already match them.

## Why one agent, many tools

See README.md. Practical consequence: there is ONE agent loop (owned by Dev B). Dev A and Dev C do
not write any agent reasoning — they write plain Python functions that the loop calls as tools.
Your functions should have zero knowledge of the LLM, the loop, or each other. Pure input -> output.

## Tool contracts

All tools take and return plain JSON-serializable Python dicts. No custom classes across the
boundary — this is what lets three people build in parallel without import hell.

### Owned by Dev A (`pipeline/`)

```python
def get_recent_logs(job_id: str) -> dict:
    """
    Returns:
    {
      "job_id": str,
      "status": "failed" | "success",
      "error_type": str,          # e.g. "schema_mismatch", "null_spike", "timeout"
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

### Owned by Dev B (`agent/`)

No tools — owns the loop that calls everyone else's tools, plus these two model-only reasoning
steps (not real "tools", just prompted reasoning inside the loop):

- **diagnose**: given logs + schema + past incidents, produce `{diagnosis, proposed_fix, confidence}`
- **critique_own_fix**: given the above, produce `{agrees: bool, counter_argument: str, revised_confidence: float}`

### Owned by Dev C (`memory_approval/`)

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
    BLOCKING call — pauses until a human clicks approve/reject in the browser.
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

## Stop conditions (Dev B enforces, everyone should know them)

- Max 8 tool calls per incident.
- Loop must terminate in one of: `fixed`, `needs_human`, `gave_up`.
- If `request_approval` returns `approved: false`, the loop ends in `needs_human` — it never
  retries a rejected fix automatically.

## Failure scenarios to support (Dev A builds these, everyone tests against them)

1. `schema_drift` — a column type changes upstream, downstream job fails on cast.
2. `null_spike` — a required field starts arriving null above a threshold.
3. `timeout` — a job exceeds its expected runtime and gets killed.

Pick whichever ONE of these is most reliable for the actual live demo. Test it more than the others.
