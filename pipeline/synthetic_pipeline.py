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
    """Simulates running the pipeline. Reflects whatever failure state is currently injected."""
    job = STATE[job_id]
    job["timestamp"] = datetime.now(timezone.utc).isoformat()
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
