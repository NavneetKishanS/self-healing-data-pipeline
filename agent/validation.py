"""Basic JSON boundary checks. Tool-specific mutation allowlists remain Dev A's responsibility."""

import hashlib
import json
import math


def fingerprint(fix: dict) -> str:
    return hashlib.sha256(json.dumps(
        fix, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _confidence(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Confidence must be finite and between 0 and 1")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty text")


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop the opening fence (optionally tagged, e.g. ```json) and a trailing ``` if present.
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -len("```")]
    return body.strip()


def parse_response(stage: str, text: str) -> dict:
    if not isinstance(text, str):
        raise ValueError("Model caller must return JSON text")
    data = json.loads(_strip_code_fence(text))
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    if stage == "diagnose":
        if set(data) != {"diagnosis", "proposed_fix", "confidence"}:
            raise ValueError("Expected diagnosis, proposed_fix, confidence")
        _text(data["diagnosis"])
        _confidence(data["confidence"])
        fix = data["proposed_fix"]
        if not isinstance(fix, dict) or set(fix) != {"fix_type", "target", "change"}:
            raise ValueError("Fix requires fix_type, target, change")
        if fix["fix_type"] not in ("schema_patch", "config_change", "retry_policy"):
            raise ValueError("Unsupported fix type")
        _text(fix["target"])
        if not isinstance(fix["change"], dict) or not fix["change"]:
            raise ValueError("Fix change must be a nonempty object")
        fingerprint(fix)
    elif stage == "critique":
        if set(data) != {"agrees", "counter_argument", "revised_confidence"}:
            raise ValueError("Expected agrees, counter_argument, revised_confidence")
        if type(data["agrees"]) is not bool:
            raise ValueError("agrees must be a boolean")
        _text(data["counter_argument"])
        _confidence(data["revised_confidence"])
    else:
        raise ValueError("Unknown reasoning stage")
    return data
