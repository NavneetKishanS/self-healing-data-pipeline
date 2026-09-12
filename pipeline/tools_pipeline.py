"""
Dev A owns this file.

Real implementations of the four tool contracts defined in CONTEXT.md.
These read/write the module-level state in synthetic_pipeline.py.
Do not change these function signatures without updating CONTEXT.md and telling Dev B/C.
"""

from pipeline.synthetic_pipeline import STATE, SCHEMA, run_pipeline, reset


def get_recent_logs(job_id: str) -> dict:
    job = STATE.get(job_id)
    if not job:
        return {"job_id": job_id, "status": "unknown", "error_type": None,
                "error_message": "No such job.", "timestamp": "", "affected_table": "",
                "row_count": 0, "expected_row_count": 0}
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


def get_schema(table_name: str) -> dict:
    schema = SCHEMA.get(table_name)
    if not schema:
        return {"table_name": table_name, "columns": [], "last_changed": None}
    return {
        "table_name": table_name,
        "columns": schema["columns"],
        "last_changed": schema["last_changed"],
    }


def apply_fix(fix: dict) -> dict:
    """
    fix = {"fix_type": ..., "target": ..., "change": {...}}
    Minimal real behavior for the demo: schema_patch reverts the drifted column type,
    null_spike/timeout fixes just reset the job to success. Extend as needed.
    """
    fix_type = fix.get("fix_type")
    target = fix.get("target", "orders")

    if fix_type == "schema_patch":
        for col in SCHEMA.get(target, {}).get("columns", []):
            if col["name"] == fix.get("change", {}).get("column", "amount"):
                col["type"] = fix["change"].get("new_type", "float")
        return {"applied": True, "message": f"Patched schema for {target}."}

    if fix_type in ("config_change", "retry_policy"):
        return {"applied": True, "message": f"Applied {fix_type} to {target}."}

    return {"applied": False, "message": f"Unknown fix_type: {fix_type}"}


def rerun_pipeline(job_id: str) -> dict:
    reset(job_id)
    job = run_pipeline(job_id)
    return {"job_id": job_id, "status": job["status"], "row_count": job["row_count"]}
