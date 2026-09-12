"""Boundary validation; action-specific allowlists remain Dev A's responsibility."""
import hashlib
import json
import math


def nonempty(value, label, limit=8000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must be a nonempty string of at most {limit} characters")
    return value


def validate_fix(fix):
    if not isinstance(fix, dict) or set(fix) != {"fix_type", "target", "change"}:
        raise ValueError("fix requires exactly fix_type, target and change")
    if fix["fix_type"] not in ("schema_patch", "config_change", "retry_policy"):
        raise ValueError("Unsupported fix_type")
    nonempty(fix["target"], "target", 200)
    if not isinstance(fix["change"], dict):
        raise ValueError("change must be a dict")
    encoded = json.dumps(fix, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded) > 16000:
        raise ValueError("Fix is too large")
    return json.loads(encoded)


def fix_hash(fix):
    checked = validate_fix(fix)
    encoded = json.dumps(checked, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_confidence(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("confidence must be finite and between 0 and 1")
    return float(value)
