"""
Dev A owns this file.

Fast, no-API-key sanity check for the pipeline/failures/tools trio, run before any demo
rehearsal. Exercises pipeline/tools_pipeline.py directly (no agent, no model calls) against
the shared fixture in pipeline/fixtures/orders.csv. Exit code 0 = all checks passed.

Usage:
    python -m pipeline.smoke_test
"""

import sys

from pipeline.synthetic_pipeline import ORDERS, reset
from pipeline.failures import inject
from pipeline.tools_pipeline import get_recent_logs, get_schema, apply_fix, rerun_pipeline

FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def test_fixture_backed_row_counts():
    reset()
    logs = get_recent_logs("job_1")
    check(
        "row counts derive from the real fixture, not a magic number",
        logs["row_count"] == len(ORDERS) and logs["expected_row_count"] == len(ORDERS),
        f"expected {len(ORDERS)}, got row_count={logs['row_count']}",
    )


def test_schema_drift_determinism():
    seen = set()
    for _ in range(5):
        reset()
        inject("schema_drift")
        logs = get_recent_logs("job_1")
        seen.add((logs["status"], logs["error_type"], logs["error_message"], logs["row_count"]))
    check("schema_drift: 5 runs produce identical output", len(seen) == 1, f"saw {len(seen)} distinct results")


def test_schema_drift_real_fix_and_rerun():
    reset()
    inject("schema_drift")
    before = get_recent_logs("job_1")
    check("schema_drift: injected failure reports correctly", before["status"] == "failed" and before["error_type"] == "schema_drift")

    fix = apply_fix({"fix_type": "schema_patch", "target": "orders", "change": {"column": "amount", "new_type": "float"}})
    check("schema_drift: apply_fix reports applied", fix["applied"] is True)

    rerun = rerun_pipeline("job_1")
    check("schema_drift: rerun reflects real fix", rerun["status"] == "success" and rerun["row_count"] == len(ORDERS))


def test_schema_drift_rerun_without_fix_still_fails():
    reset()
    inject("schema_drift")
    rerun = rerun_pipeline("job_1")
    check("schema_drift: rerun WITHOUT a fix still fails (no fake success)", rerun["status"] == "failed" and rerun["row_count"] == 0)


def test_null_spike_and_timeout_round_trip():
    for scenario in ("null_spike", "timeout"):
        reset()
        inject(scenario)
        logs = get_recent_logs("job_1")
        check(f"{scenario}: injected failure has correct error_type", logs["error_type"] == scenario)
        # Known gap (documented in CONTEXT.md §6): rerun always reports success here,
        # regardless of a real fix. Only asserting the round-trip shape, not correctness.
        rerun = rerun_pipeline("job_1")
        check(f"{scenario}: rerun_pipeline returns the contract shape", set(rerun) == {"job_id", "status", "row_count"})


def test_get_schema_contract_shape():
    reset()
    schema = get_schema("orders")
    check(
        "get_schema returns the contract shape",
        set(schema) == {"table_name", "columns", "last_changed"} and isinstance(schema["columns"], list),
    )


if __name__ == "__main__":
    test_fixture_backed_row_counts()
    test_schema_drift_determinism()
    test_schema_drift_real_fix_and_rerun()
    test_schema_drift_rerun_without_fix_still_fails()
    test_null_spike_and_timeout_round_trip()
    test_get_schema_contract_shape()

    reset()  # leave shared state (STATE + SCHEMA) clean for whoever runs next

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")
