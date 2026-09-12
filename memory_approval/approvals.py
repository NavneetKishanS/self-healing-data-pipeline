"""Durable approval records. Human approval never executes pipeline tools here."""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import secrets
import time
import uuid
import math

from memory_approval.storage import runtime_path, transaction
from memory_approval.validation import fix_hash, nonempty, validate_confidence, validate_fix

_incident = ContextVar("approval_incident", default=None)


@contextmanager
def approval_context(incident_id):
    """Supply Dev B's incident ID without changing request_approval's contract."""
    nonempty(incident_id, "incident_id", 200)
    token = _incident.set(incident_id)
    try:
        yield
    finally:
        _incident.reset(token)


def _expire(records):
    for record in records.values():
        if record["status"] in ("pending", "approved") and time.time() >= record["expires_at"]:
            record.update(status="expired", human_note="Timed out waiting for approval or application.")


def create_approval(diagnosis, proposed_fix, confidence, timeout_s):
    nonempty(diagnosis, "diagnosis")
    fix = validate_fix(proposed_fix)
    confidence = validate_confidence(confidence)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be finite and positive")
    record = {
        "approval_id": uuid.uuid4().hex,
        "incident_id": _incident.get() or "local-" + uuid.uuid4().hex,
        "diagnosis": diagnosis, "proposed_fix": fix, "confidence": confidence,
        "fix_hash": fix_hash(fix), "decision_token": secrets.token_urlsafe(32),
        "created_at": time.time(), "expires_at": time.time() + timeout_s,
        "status": "pending", "human_note": "", "decided_by": None, "decision_time": None,
    }
    with transaction(runtime_path("approvals.json"), dict) as records:
        _expire(records)
        if any(r["incident_id"] == record["incident_id"] for r in records.values()):
            raise ValueError("Incident already has an approval record; use a new incident for a new trial")
        records[record["approval_id"]] = record
    return copy.deepcopy(record)


def get_approval(approval_id):
    with transaction(runtime_path("approvals.json"), dict) as records:
        _expire(records)
        return copy.deepcopy(records.get(approval_id))


def list_pending():
    with transaction(runtime_path("approvals.json"), dict) as records:
        _expire(records)
        return copy.deepcopy([r for r in records.values() if r["status"] == "pending"])


def decide_approval(payload, subject="local-operator"):
    if not isinstance(payload, dict) or payload.get("decision") not in ("approve", "reject"):
        raise ValueError("decision must be approve or reject")
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note) > 2000:
        raise ValueError("note must be a string of at most 2000 characters")
    with transaction(runtime_path("approvals.json"), dict) as records:
        _expire(records)
        approval_id = payload.get("approval_id")
        if not isinstance(approval_id, str):
            return False
        record = records.get(approval_id)
        if not record or record["status"] != "pending":
            return False
        for field in ("incident_id", "fix_hash", "decision_token"):
            supplied = payload.get(field)
            if not isinstance(supplied, str) or not secrets.compare_digest(supplied, record[field]):
                return False
        record.update(status="approved" if payload["decision"] == "approve" else "rejected",
                      human_note=note, decided_by=subject, decision_time=time.time())
    return True


def consume_approval(incident_id, fix):
    """Claim once immediately BEFORE apply_fix. After uncertain mutation, never replay.

    Additive orchestration hook; Dev B must call it. This does not wrap Dev A's tools.
    """
    digest = fix_hash(fix)
    with transaction(runtime_path("approvals.json"), dict) as records:
        _expire(records)
        matches = [r for r in records.values() if r["incident_id"] == incident_id]
        if len(matches) != 1:
            return False
        record = matches[0]
        if record["status"] != "approved" or not secrets.compare_digest(record["fix_hash"], digest):
            return False
        record.update(status="claimed", claimed_at=time.time())
        return True
