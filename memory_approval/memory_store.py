"""
Dev C owns this file.

JSON-backed incident memory. No embeddings, no graph DB — a keyword match on error_type
is enough for 5 seeded records. This is the honest, right-sized version for a 6-hour build.
"""

import json
from pathlib import Path
from memory_approval.storage import runtime_path, transaction
from memory_approval.validation import nonempty, validate_fix

SEED_PATH = Path(__file__).with_name("incidents_seed.json")


def _validate(incident):
    required = {"incident_id", "error_type", "root_cause", "fix_applied", "outcome"}
    if not isinstance(incident, dict) or set(incident) != required:
        raise ValueError("Incident fields do not match CONTEXT.md")
    for key in ("incident_id", "error_type", "root_cause"):
        nonempty(incident[key], key)
    if incident["outcome"] not in ("resolved", "reverted", "recurred"):
        raise ValueError("Unsupported historical outcome")
    validate_fix(incident["fix_applied"])


def _check_records(records):
    if not isinstance(records, list):
        raise ValueError("Incident store must contain a JSON list")
    seen = set()
    for record in records:
        _validate(record)
        if record["incident_id"] in seen:
            raise ValueError("Duplicate incident ID in stored history")
        seen.add(record["incident_id"])


def _seeds():
    with SEED_PATH.open(encoding="utf-8") as source:
        records = json.load(source)
    _check_records(records)
    return records


def search_past_incidents(error_type: str) -> dict:
    nonempty(error_type, "error_type", 200)
    seeds = _seeds()
    with transaction(runtime_path("incidents.json"), list) as records:
        _check_records(records)
        merged = {r["incident_id"]: r for r in seeds}
        for record in records:
            if record["incident_id"] in merged and record != merged[record["incident_id"]]:
                raise ValueError("Runtime incident conflicts with seed history")
            merged[record["incident_id"]] = record
        matches = [r for r in merged.values() if r["error_type"] == error_type]
        return {"matches": json.loads(json.dumps(matches[-10:]))}


def log_incident(incident: dict) -> dict:
    try:
        _validate(incident)
        seeds = _seeds()
        with transaction(runtime_path("incidents.json"), list) as records:
            _check_records(records)
            for record in seeds + records:
                if record["incident_id"] == incident["incident_id"]:
                    return {"logged": record == incident}
            records.append(json.loads(json.dumps(incident, allow_nan=False)))
        return {"logged": True}
    except (ValueError, TypeError, OSError):
        return {"logged": False}
