"""
Dev A owns this file.

A tiny in-memory pipeline with three chained steps: extract -> transform -> load.
State is kept in-process (module-level dict) so the tool functions in tools_pipeline.py
can read it. Deliberately simple — the point is reproducible failure, not realism.
"""

import argparse
import copy
import csv
from datetime import datetime, timezone
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "orders.csv"


def _load_orders_fixture() -> list[dict]:
    """
    Shared team fixture (pipeline/fixtures/orders.csv) so Dev A/B/C all sanity-check
    against the same real row counts instead of a magic number.
    """
    with open(FIXTURE_PATH, newline="") as f:
        return list(csv.DictReader(f))


ORDERS = _load_orders_fixture()

# ---- module-level "database" the whole demo reads/writes ----
STATE = {
    "job_1": {
        "status": "success",
        "error_type": None,
        "error_message": "",
        "affected_table": "orders",
        "row_count": len(ORDERS),
        "expected_row_count": len(ORDERS),
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
SCHEMA_PRISTINE = copy.deepcopy(SCHEMA)


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
    """
    Clears STATE only. Deliberately does NOT touch SCHEMA — tools_pipeline.rerun_pipeline
    calls this before re-running, and SCHEMA must survive that call so a rerun genuinely
    fails when schema_drift hasn't actually been fixed (see run_pipeline's docstring).
    Use restore_schema() separately when starting a brand new failure scenario.
    """
    STATE[job_id].update({
        "status": "success",
        "error_type": None,
        "error_message": "",
        "row_count": STATE[job_id]["expected_row_count"],
    })


def restore_schema() -> None:
    """
    Restores SCHEMA to its pristine (undrifted) state. Call this before injecting a new
    failure scenario — not before a rerun — so an unresolved schema_drift left over from
    a previous scenario in the same process can't silently corrupt the next one.
    """
    SCHEMA["orders"]["columns"] = copy.deepcopy(SCHEMA_PRISTINE["orders"]["columns"])
    SCHEMA["orders"]["last_changed"] = SCHEMA_PRISTINE["orders"]["last_changed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inject-failure", choices=["schema_drift", "null_spike", "timeout"])
    args = parser.parse_args()

    if args.inject_failure:
        from pipeline.failures import inject
        inject(args.inject_failure)

    run_pipeline()
