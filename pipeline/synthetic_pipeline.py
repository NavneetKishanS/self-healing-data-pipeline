"""
Dev A owns this file.

A tiny in-memory pipeline with three chained steps: extract -> transform -> load.
State is kept in-process (module-level dict) so the tool functions in tools_pipeline.py
can read it. Deliberately simple — the point is reproducible failure, not realism.
"""

import argparse
from datetime import datetime, timezone

# ---- module-level "database" the whole demo reads/writes ----
STATE = {
    "job_1": {
        "status": "success",
        "error_type": None,
        "error_message": "",
        "affected_table": "orders",
        "row_count": 1000,
        "expected_row_count": 1000,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
}

SCHEMA = {
    "orders": {
        "columns": [
            {"name": "order_id", "type": "string", "nullable": False},
            {"name": "customer_id", "type": "string", "nullable": False},
            {"name": "amount", "type": "float", "nullable": False},
            {"name": "created_at", "type": "timestamp", "nullable": False},
        ],
        "last_changed": None,
    }
}


def run_pipeline(job_id: str = "job_1") -> dict:
    """
    Simulates running the pipeline. Re-validates against real current state (schema,
    in particular) rather than trusting a status flag someone else already set — a
    rerun must actually fail if the underlying condition wasn't fixed, otherwise a
    wrong fix would falsely report success.
    """
    job = STATE[job_id]
    job["timestamp"] = datetime.now(timezone.utc).isoformat()

    amount_col = next(
        (c for c in SCHEMA.get(job["affected_table"], {}).get("columns", []) if c["name"] == "amount"),
        None,
    )
    if amount_col is not None and amount_col["type"] != "float":
        job.update({
            "status": "failed",
            "error_type": "schema_drift",
            "error_message": "Cannot cast column 'amount' (string) to float during transform step.",
            "row_count": 0,
        })
    elif job["status"] != "failed" or job["error_type"] == "schema_drift":
        # Only auto-clear schema_drift here; null_spike/timeout have no re-checkable
        # condition in this scaffold and are cleared by reset() before this runs.
        job.update({
            "status": "success",
            "error_type": None,
            "error_message": "",
            "row_count": job["expected_row_count"],
        })

    if job["status"] == "failed":
        print(f"[pipeline] {job_id} FAILED — {job['error_type']}: {job['error_message']}")
    else:
        print(f"[pipeline] {job_id} succeeded — {job['row_count']} rows")
    return job


def reset(job_id: str = "job_1") -> None:
    STATE[job_id].update({
        "status": "success",
        "error_type": None,
        "error_message": "",
        "row_count": STATE[job_id]["expected_row_count"],
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inject-failure", choices=["schema_drift", "null_spike", "timeout"])
    args = parser.parse_args()

    if args.inject_failure:
        from pipeline.failures import inject
        inject(args.inject_failure)

    run_pipeline()
