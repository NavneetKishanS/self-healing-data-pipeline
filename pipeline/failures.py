"""
Dev A owns this file.

One function per failure scenario from CONTEXT.md. Each mutates STATE/SCHEMA in
synthetic_pipeline.py so the tool functions in tools_pipeline.py report it accurately.
Keep these deterministic — no randomness. A demo failure must reproduce identically every time.
"""

from pipeline.synthetic_pipeline import STATE, SCHEMA


def inject_schema_drift(job_id: str = "job_1") -> None:
    """Upstream 'amount' column silently changed from float to string."""
    SCHEMA["orders"]["columns"][2]["type"] = "string"
    SCHEMA["orders"]["last_changed"] = "2026-09-12T08:00:00Z"
    STATE[job_id].update({
        "status": "failed",
        "error_type": "schema_drift",
        "error_message": "Cannot cast column 'amount' (string) to float during transform step.",
    })


def inject_null_spike(job_id: str = "job_1") -> None:
    """customer_id starts arriving null above an acceptable threshold."""
    STATE[job_id].update({
        "status": "failed",
        "error_type": "null_spike",
        "error_message": "42% of rows have null customer_id, exceeds 5% threshold.",
        "row_count": 580,
    })


def inject_timeout(job_id: str = "job_1") -> None:
    """Job exceeds its expected runtime and is killed."""
    STATE[job_id].update({
        "status": "failed",
        "error_type": "timeout",
        "error_message": "Job exceeded 300s runtime limit during load step, killed.",
        "row_count": 0,
    })


SCENARIOS = {
    "schema_drift": inject_schema_drift,
    "null_spike": inject_null_spike,
    "timeout": inject_timeout,
}


def inject(name: str, job_id: str = "job_1") -> None:
    if name not in SCENARIOS:
        raise ValueError(f"Unknown failure scenario: {name}. Options: {list(SCENARIOS)}")
    SCENARIOS[name](job_id)
