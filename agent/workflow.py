"""A fixed, synchronous incident workflow with explicit tool and model dependencies."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from time import monotonic
from typing import Callable, Mapping
from uuid import uuid4

from . import prompts
from .errors import ModelRequestError
from .validation import fingerprint, parse_response

MAX_TOOL_CALLS = 8
MAX_MODEL_CALLS = 3
REQUIRED_TOOLS = (
    "get_recent_logs", "get_schema", "search_past_incidents", "request_approval",
    "apply_fix", "rerun_pipeline", "log_incident",
)
class _Stop(Exception):
    def __init__(self, outcome, reason, event):
        self.outcome, self.reason, self.event = outcome, reason, event


def run_incident(
    job_id: str, *, tools: Mapping[str, Callable], model: Callable,
    incident_id: str = None, procedures=None,
) -> dict:
    """Run the CONTEXT.md sequence using supplied functions.

    model(system=str, prompt=str, **hints) -> JSON text; one reasoning request per call. The hints
    (stage, fast_path, correction) let a client route between configured models; a client may retry
    a request that produced no answer (rate limit, outage, empty reply) against a fallback model,
    never a request whose answer was malformed.
    tools use exactly the keyword names in CONTEXT.md.
    procedures (optional) is a procedural graph exposing localize(stage, error_type, proposal)
    and refine(result); see memory_approval/procedural_graph.py. It shapes the two prompts and
    blocks proposals it has pruned, learns from the terminal record after the outcome is fixed,
    and never adds a tool or model call. Its failures become warnings, not outcomes.
    Configuration errors raise ValueError before execution; operational failures return a
    terminal result. Callers must serialize runs sharing mutable pipeline/approval state.
    """
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id must be nonempty")
    if incident_id is not None and (not isinstance(incident_id, str) or not incident_id.strip()):
        raise ValueError("incident_id must be nonempty")
    missing = [name for name in REQUIRED_TOOLS if not callable(tools.get(name))]
    if missing or not callable(model):
        raise ValueError("Supply a model callable and all required tools: " + ", ".join(missing))
    if procedures is not None and not all(callable(getattr(procedures, name, None)) for name in ("localize", "refine")):
        raise ValueError("procedures must provide localize and refine")
    dispatch = dict(tools)
    result = {
        "incident_id": incident_id or str(uuid4()), "job_id": job_id,
        "outcome": None, "reason": "", "tool_calls": 0, "model_calls": 0,
        "error_type": None, "terminal_event": None,
        "diagnosis": None, "critique": None, "approval": None, "application": None,
        "verification": None, "memory_logged": False, "sources": [],
        "mutation_state": "not_attempted", "trace": [], "warnings": [],
        "prompt_versions": prompts.versions(),
        "procedures": None if procedures is None else {"revision": None, "fast_path": None,
                                                        "guard": None, "refinement": None},
    }
    started = monotonic()
    stage = "evidence"
    correction_used = False

    def record(kind, name, status):
        # No raw tool inputs, model output, or exception messages in the trace.
        result["trace"].append({"kind": kind, "name": name, "status": status,
                                "elapsed_seconds": round(monotonic() - started, 6)})

    def tool(name, **arguments):
        if result["tool_calls"] >= MAX_TOOL_CALLS:
            raise _Stop("needs_human" if result["mutation_state"] != "not_attempted" else "gave_up",
                        "Tool-call budget exhausted", "budget_exhausted")
        result["tool_calls"] += 1
        try:
            value = dispatch[name](**arguments)
            if not isinstance(value, dict):
                raise ValueError("Tool must return a dict")
            # Snapshot the JSON boundary so later tool mutations cannot rewrite evidence.
            value = json.loads(json.dumps(value, allow_nan=False))
        except Exception:
            record("tool", name, "error")
            raise
        record("tool", name, "returned")
        return value

    def reason(name, evidence, proposal=None, guidance=None):
        nonlocal correction_used
        prompt = prompts.render(name, evidence=evidence, proposal=proposal,
                                procedures=guidance["procedures"] if guidance else None)
        hints = {"stage": name, "fast_path": bool(guidance and guidance.get("fast_path")), "correction": False}
        while True:
            if result["model_calls"] >= MAX_MODEL_CALLS:
                raise _Stop("gave_up", "Model-call budget exhausted", "budget_exhausted")
            result["model_calls"] += 1
            try:
                text = model(system=prompts.render("system"), prompt=prompt, **hints)
            except Exception:
                record("model", name, "error")
                raise
            try:
                parsed = parse_response(name, text)
            except ValueError as exc:
                record("model", name, "invalid")
                if correction_used:
                    raise _Stop("gave_up", "Model output remained invalid after the shared correction allowance", "model_invalid")
                correction_used = True
                hints["correction"] = True
                prompt += "\nCorrection: " + str(exc) + ". Return only the required JSON object."
            else:
                record("model", name, "valid")
                return parsed

    def localize(name, error_type, proposal=None):
        # Not a tool call: no budget, no evidence. Only the graph's own view reaches the prompt.
        if procedures is None:
            return None
        try:
            view = json.loads(json.dumps(procedures.localize(name, error_type, proposal), allow_nan=False))
            if (not isinstance(view, dict) or not isinstance(view.get("procedures"), dict)
                    or not isinstance(view.get("pruned_fix_types"), list)):
                raise ValueError("Localized procedures must be an object with procedures and pruned_fix_types")
        except Exception:
            record("procedure", name, "unavailable")
            result["warnings"].append("Procedural graph unavailable for " + name + "; proceeding on evidence alone")
            return None
        record("procedure", name, "localized")
        result["procedures"].update(revision=view.get("revision"), fast_path=view.get("fast_path"))
        return view

    try:
        logs = tool("get_recent_logs", job_id=job_id)
        if logs.get("job_id") != job_id or logs.get("status") != "failed":
            raise _Stop("needs_human", "No matching failed job was reported; no repair attempted", "job_not_failed")
        table, error_type = logs.get("affected_table"), logs.get("error_type")
        expected = logs.get("expected_row_count")
        if (not isinstance(table, str) or not table or not isinstance(error_type, str)
                or not error_type or type(expected) is not int or expected < 0):
            raise _Stop("needs_human", "Failure evidence lacks a table, error type, or expected row count", "invalid_evidence")
        result["error_type"] = error_type
        schema = tool("get_schema", table_name=table)
        if schema.get("table_name") != table or not isinstance(schema.get("columns"), list):
            raise _Stop("needs_human", "Schema evidence does not match the affected table", "invalid_evidence")
        history = tool("search_past_incidents", error_type=error_type)
        if not isinstance(history.get("matches"), list):
            raise _Stop("needs_human", "Incident memory returned an invalid matches list", "invalid_evidence")
        evidence = {"logs": logs, "schema": schema, "past_incidents": history}
        stage = "diagnose"
        guidance = localize("diagnose", error_type)
        proposal = reason("diagnose", evidence, guidance=guidance)
        result["diagnosis"] = deepcopy(proposal)
        fix = deepcopy(proposal["proposed_fix"])
        if fix["fix_type"] == "schema_patch" and fix["target"] != table:
            raise _Stop("needs_human", "Schema repair targets a different table", "proposal_off_target")
        if guidance and fix["fix_type"] in guidance["pruned_fix_types"]:
            # Structural enforcement of earlier human rejections and verified failures: the
            # model never gets a second chance at a pruned repair, and no approval is requested.
            result["procedures"]["guard"] = "blocked"
            raise _Stop("needs_human", "Proposed repair matches a procedure pruned by earlier human review or verified failure",
                        "proposal_pruned")
        stage = "critique"
        critique = reason("critique", evidence, proposal, guidance=localize("critique", error_type, fix))
        result["critique"] = critique
        if not critique["agrees"]:
            raise _Stop("needs_human", "Critique does not support applying the proposed repair", "critique_disagreed")

        stage = "approval"
        approved_hash = fingerprint(fix)
        approval_payload = deepcopy(fix)
        approval = tool("request_approval", diagnosis=proposal["diagnosis"],
                        proposed_fix=approval_payload, confidence=critique["revised_confidence"])
        result["approval"] = {
            "incident_id": result["incident_id"], "fix_hash": approved_hash,
            "approved": approval.get("approved") is True,
            "human_note": approval.get("human_note", ""),
            "subject": approval.get("subject") if isinstance(approval.get("subject"), str) else None,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        if approval.get("approved") is not True:
            if approval.get("expired") is True:
                # Nobody decided. That is not a rejection, so the procedural graph must not learn from it.
                raise _Stop("needs_human", "No human decision arrived before the approval expired", "approval_expired")
            raise _Stop("needs_human", "Repair was not approved", "rejected")
        if fingerprint(approval_payload) != approved_hash:
            result["approval"]["approved"] = False
            raise _Stop("needs_human", "Approval handler changed the proposed fix; a fresh approval is required",
                        "approval_tampered")

        stage = "apply"
        result["mutation_state"] = "uncertain"
        applied = tool("apply_fix", fix=deepcopy(fix))
        message = applied.get("message")
        result["application"] = {"applied": applied.get("applied") is True,
                                 "message": message[:300] if isinstance(message, str) else ""}
        if applied.get("applied") is not True:
            result["mutation_state"] = "not_confirmed"
            raise _Stop("needs_human", "Tool did not confirm the repair; no rerun attempted", "apply_failed")
        result["mutation_state"] = "applied"
        stage = "verification"
        verification = tool("rerun_pipeline", job_id=job_id)
        result["verification"] = verification
        if (verification.get("job_id") != job_id or verification.get("status") != "success"
                or type(verification.get("row_count")) is not int
                or verification["row_count"] != expected):
            raise _Stop("needs_human", "Rerun did not pass the job status and expected-row-count checks",
                        "verification_failed")

        stage = "memory"
        incident = {"incident_id": result["incident_id"], "error_type": error_type,
                    "root_cause": proposal["diagnosis"], "fix_applied": fix, "outcome": "resolved"}
        try:
            logged = tool("log_incident", incident=deepcopy(incident))
            result["memory_logged"] = logged.get("logged") is True
        except Exception:
            pass  # Recovery was verified. Memory delivery is reported separately below.
        if not result["memory_logged"]:
            result["warnings"].append("Recovery verified but incident memory was not confirmed written; do not replay the repair")
        result["outcome"] = "fixed"
        result["reason"] = "Rerun succeeded with the expected row count"
        result["terminal_event"] = "verified"
    except _Stop as stop:
        result["outcome"], result["reason"], result["terminal_event"] = stop.outcome, stop.reason, stop.event
    except ModelRequestError as exc:
        result["outcome"] = "gave_up"
        result["reason"] = f"{stage}: {exc}; no automatic retry"
        result["terminal_event"] = "model_error"
    except Exception as exc:
        result["outcome"] = "needs_human" if stage in ("approval", "apply", "verification", "memory") else "gave_up"
        result["reason"] = f"{stage} failed ({type(exc).__name__}); no automatic retry"
        result["terminal_event"] = "stage_error"
    record("incident", "terminal", result["outcome"])
    if procedures is not None:
        # Offline evolution happens after the outcome is final and cannot change it.
        try:
            refinement = json.loads(json.dumps(procedures.refine(deepcopy(result)), allow_nan=False))
            if not isinstance(refinement, dict):
                raise ValueError("refine must return an object")
            result["procedures"]["refinement"] = refinement
            record("procedure", "refine", "committed" if refinement.get("committed") is True else "unchanged")
        except Exception:
            record("procedure", "refine", "error")
            result["warnings"].append("Procedural graph was not refined from this incident")
    return result
