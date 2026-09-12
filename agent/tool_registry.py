"""
Dev B owns this file.

Combines every tool from pipeline/ and memory_approval/ into one registry the agent loop
calls. USE_STUBS lets you build and test your loop before Dev A / Dev C have real code —
the stub functions below return realistic fake data matching the exact shapes in CONTEXT.md.

Flip USE_STUBS to False once real implementations land. Nothing else in agent_loop.py
should need to change if everyone respected the contract in CONTEXT.md.
"""

USE_STUBS = True

# ---- Anthropic tool-calling schemas (JSON schema per tool) ----
TOOL_SCHEMAS = [
    {
        "name": "get_recent_logs",
        "description": "Get the most recent failure logs for a pipeline job.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "get_schema",
        "description": "Get the current column schema for a table.",
        "input_schema": {
            "type": "object",
            "properties": {"table_name": {"type": "string"}},
            "required": ["table_name"],
        },
    },
    {
        "name": "search_past_incidents",
        "description": "Search incident memory for past failures of a given error type and how they were fixed.",
        "input_schema": {
            "type": "object",
            "properties": {"error_type": {"type": "string"}},
            "required": ["error_type"],
        },
    },
    {
        "name": "request_approval",
        "description": "Ask a human to approve or reject a proposed fix before it is applied. Blocking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "diagnosis": {"type": "string"},
                "proposed_fix": {"type": "object"},
                "confidence": {"type": "number"},
            },
            "required": ["diagnosis", "proposed_fix", "confidence"],
        },
    },
    {
        "name": "apply_fix",
        "description": "Apply an approved fix to the pipeline. Only call after request_approval returns approved=true.",
        "input_schema": {
            "type": "object",
            "properties": {"fix": {"type": "object"}},
            "required": ["fix"],
        },
    },
    {
        "name": "rerun_pipeline",
        "description": "Rerun a pipeline job after a fix has been applied.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "log_incident",
        "description": "Log the outcome of a resolved incident back into memory for future reference.",
        "input_schema": {
            "type": "object",
            "properties": {"incident": {"type": "object"}},
            "required": ["incident"],
        },
    },
]


# ---- Stub implementations (realistic fake data, matches CONTEXT.md shapes exactly) ----
def _stub_get_recent_logs(job_id: str) -> dict:
    # Try to read from real STATE if available, else use hardcoded defaults
    try:
        from pipeline.synthetic_pipeline import STATE
        job = STATE.get(job_id)
        if job:
            return {
                "job_id": job_id,
                "status": job["status"],
                "error_type": job["error_type"],
                "error_message": job["error_message"],
                "timestamp": job["timestamp"],
                "affected_table": job["affected_table"],
                "row_count": job["row_count"],
                "expected_row_count": job["expected_row_count"],
            }
    except ImportError:
        pass

    # Fallback
    return {
        "job_id": job_id, "status": "failed", "error_type": "schema_drift",
        "error_message": "Cannot cast column 'amount' (string) to float during transform step.",
        "timestamp": "2026-09-12T08:00:00Z", "affected_table": "orders",
        "row_count": 1000, "expected_row_count": 1000,
    }


def _stub_get_schema(table_name: str) -> dict:
    # Try to read from real SCHEMA if available, else use hardcoded defaults
    try:
        from pipeline.synthetic_pipeline import SCHEMA
        schema = SCHEMA.get(table_name)
        if schema:
            return {
                "table_name": table_name,
                "columns": schema["columns"],
                "last_changed": schema["last_changed"],
            }
    except ImportError:
        pass

    # Fallback
    return {
        "table_name": table_name,
        "columns": [
            {"name": "order_id", "type": "string", "nullable": False},
            {"name": "amount", "type": "string", "nullable": False},
        ],
        "last_changed": "2026-09-12T08:00:00Z",
    }


def _stub_search_past_incidents(error_type: str) -> dict:
    # Try to read from real memory store if available
    try:
        from memory_approval.memory_store import search_past_incidents
        return search_past_incidents(error_type)
    except ImportError:
        pass

    # Fallback
    return {"matches": [{
        "incident_id": "inc_stub", "error_type": error_type,
        "root_cause": "Stub: similar drift fixed previously by patching the column type.",
        "fix_applied": {"fix_type": "schema_patch", "target": "orders", "change": {"column": "amount", "new_type": "float"}},
        "outcome": "resolved",
    }]}


def _stub_request_approval(diagnosis: str, proposed_fix: dict, confidence: float) -> dict:
    print(f"[STUB approval] auto-approving for local testing: {diagnosis}")
    return {"approved": True, "human_note": "auto-approved (stub)"}


def _stub_apply_fix(fix: dict) -> dict:
    return {"applied": True, "message": "Stub: fix applied."}


def _stub_rerun_pipeline(job_id: str) -> dict:
    return {"job_id": job_id, "status": "success", "row_count": 1000}


def _stub_log_incident(incident: dict) -> dict:
    print(f"[STUB memory] would log: {incident}")
    return {"logged": True}


def get_dispatch_table() -> dict:
    """Returns {tool_name: callable}. Swaps stubs for real implementations based on USE_STUBS."""
    if USE_STUBS:
        return {
            "get_recent_logs": _stub_get_recent_logs,
            "get_schema": _stub_get_schema,
            "search_past_incidents": _stub_search_past_incidents,
            "request_approval": _stub_request_approval,
            "apply_fix": _stub_apply_fix,
            "rerun_pipeline": _stub_rerun_pipeline,
            "log_incident": _stub_log_incident,
        }

    # Real implementations — uncomment as each dev's code lands.
    from pipeline.tools_pipeline import get_recent_logs, get_schema, apply_fix, rerun_pipeline
    from memory_approval.memory_store import search_past_incidents, log_incident
    from memory_approval.approval_server import request_approval

    return {
        "get_recent_logs": get_recent_logs,
        "get_schema": get_schema,
        "search_past_incidents": search_past_incidents,
        "request_approval": request_approval,
        "apply_fix": apply_fix,
        "rerun_pipeline": rerun_pipeline,
        "log_incident": log_incident,
    }
