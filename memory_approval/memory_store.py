"""
Dev C owns this file.

JSON-backed incident memory. No embeddings, no graph DB — a keyword match on error_type
is enough for 5 seeded records. This is the honest, right-sized version for a 6-hour build.
"""

import json
import os

DATA_PATH = os.path.join(os.path.dirname(__file__), "incidents_seed.json")


def _load() -> list:
    with open(DATA_PATH, "r") as f:
        return json.load(f)


def _save(incidents: list) -> None:
    with open(DATA_PATH, "w") as f:
        json.dump(incidents, f, indent=2)


def search_past_incidents(error_type: str) -> dict:
    incidents = _load()
    matches = [i for i in incidents if i["error_type"] == error_type]
    return {"matches": matches}


def log_incident(incident: dict) -> dict:
    incidents = _load()
    incidents.append(incident)
    _save(incidents)
    return {"logged": True}
