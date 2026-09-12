"""A fixed, synchronous incident workflow with explicit tool and model dependencies."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from time import monotonic
from typing import Callable, Mapping
from uuid import uuid4

from . import prompts
from .validation import fingerprint, parse_response

MAX_TOOL_CALLS = 8
MAX_MODEL_CALLS = 3
REQUIRED_TOOLS = (
    "get_recent_logs", "get_schema", "search_past_incidents", "request_approval",
    "apply_fix", "rerun_pipeline", "log_incident",
)
# Deliberately public, fixed terminology. No raw log contents enter web queries.
RESEARCH_QUERIES = {
    "schema_drift": "data pipeline schema drift string to float conversion documentation",
    "schema_mismatch": "data pipeline schema mismatch string to float conversion documentation",
    "null_spike": "data pipeline required field null validation documentation",
    "timeout": "data pipeline timeout diagnosis retry policy documentation",
}


class _Stop(Exception):
    def __init__(self, outcome, reason):
        self.outcome, self.reason = outcome, reason


def run_incident(
    job_id: str, *, tools: Mapping[str, Callable], model: Callable,
    incident_id: str = None,
) -> dict:
    """Run the CONTEXT.md sequence using supplied functions.

    model(system=str, prompt=str) -> JSON text; at most one provider request per call.
    tools use exactly the keyword names in CONTEXT.md. search_repair_docs is optional.
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
    dispatch = dict(tools)
    result = {
        "incident_id": incident_id or str(uuid4()), "job_id": job_id,
        "outcome": None, "reason": "", "tool_calls": 0, "model_calls": 0,
        "diagnosis": None, "critique": None, "approval": None,
        "verification": None, "memory_logged": False, "sources": [],
        "mutation_state": "not_attempted", "trace": [], "warnings": [],
        "prompt_versions": prompts.versions(),
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
                        "Tool-call budget exhausted")
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

    def reason(name, evidence, proposal=None):
        nonlocal correction_used
        prompt = prompts.render(name, evidence=evidence, proposal=proposal)
        while True:
            if result["model_calls"] >= MAX_MODEL_CALLS:
                raise _Stop("gave_up", "Model-call budget exhausted")
            result["model_calls"] += 1
            try:
                text = model(system=prompts.render("system"), prompt=prompt)
            except Exception:
                record("model", name, "error")
                raise
            try:
                parsed = parse_response(name, text)
            except ValueError as exc:
                record("model", name, "invalid")
                if correction_used:
                    raise _Stop("gave_up", "Model output remained invalid after the shared correction allowance")
                correction_used = True
                prompt += "\nCorrection: " + str(exc) + ". Return only the required JSON object."
            else:
                record("model", name, "valid")
                return parsed

    try:
        logs = tool("get_recent_logs", job_id=job_id)
        if logs.get("job_id") != job_id or logs.get("status") != "failed":
            raise _Stop("needs_human", "No matching failed job was reported; no repair attempted")
        table, error_type = logs.get("affected_table"), logs.get("error_type")
        expected = logs.get("expected_row_count")
        if (not isinstance(table, str) or not table or not isinstance(error_type, str)
                or not error_type or type(expected) is not int or expected < 0):
            raise _Stop("needs_human", "Failure evidence lacks a table, error type, or expected row count")
        schema = tool("get_schema", table_name=table)
        if schema.get("table_name") != table or not isinstance(schema.get("columns"), list):
            raise _Stop("needs_human", "Schema evidence does not match the affected table")
        history = tool("search_past_incidents", error_type=error_type)
        if not isinstance(history.get("matches"), list):
            raise _Stop("needs_human", "Incident memory returned an invalid matches list")
        if callable(dispatch.get("search_repair_docs")) and error_type in RESEARCH_QUERIES:
            try:
                research = tool("search_repair_docs", query=RESEARCH_QUERIES[error_type])
                sources = research.get("sources")
                if research.get("status") != "ok" or not isinstance(sources, list):
                    raise ValueError("Research unavailable")
                for source in sources[:3]:
                    if (not isinstance(source, dict)
                            or not all(isinstance(source.get(k), str) for k in ("title", "url", "excerpt"))
                            or not source["url"].startswith(("https://", "http://"))):
                        raise ValueError("Invalid research source")
                result["sources"] = [{"title": s["title"][:300], "url": s["url"][:2048],
                                      "excerpt": s["excerpt"][:2000]} for s in sources[:3]]
            except Exception:
                result["warnings"].append("External research unavailable; using local evidence")
        evidence = {"logs": logs, "schema": schema, "past_incidents": history,
                    "external_sources": deepcopy(result["sources"])}
        stage = "diagnose"
        proposal = reason("diagnose", evidence)
        result["diagnosis"] = deepcopy(proposal)
        fix = deepcopy(proposal["proposed_fix"])
        if fix["fix_type"] == "schema_patch" and fix["target"] != table:
            raise _Stop("needs_human", "Schema repair targets a different table")
        stage = "critique"
        critique = reason("critique", evidence, proposal)
        result["critique"] = critique
        if not critique["agrees"]:
            raise _Stop("needs_human", "Critique does not support applying the proposed repair")

        stage = "approval"
        approved_hash = fingerprint(fix)
        approval_payload = deepcopy(fix)
        approval = tool("request_approval", diagnosis=proposal["diagnosis"],
                        proposed_fix=approval_payload, confidence=critique["revised_confidence"])
        result["approval"] = {
            "incident_id": result["incident_id"], "fix_hash": approved_hash,
            "approved": approval.get("approved") is True,
            "human_note": approval.get("human_note", ""),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        if approval.get("approved") is not True:
            raise _Stop("needs_human", "Repair was not approved")
        if fingerprint(approval_payload) != approved_hash:
            result["approval"]["approved"] = False
            raise _Stop("needs_human", "Approval handler changed the proposed fix; a fresh approval is required")

        stage = "apply"
        result["mutation_state"] = "uncertain"
        applied = tool("apply_fix", fix=deepcopy(fix))
        if applied.get("applied") is not True:
            result["mutation_state"] = "not_confirmed"
            raise _Stop("needs_human", "Tool did not confirm the repair; no rerun attempted")
        result["mutation_state"] = "applied"
        stage = "verification"
        verification = tool("rerun_pipeline", job_id=job_id)
        result["verification"] = verification
        if (verification.get("job_id") != job_id or verification.get("status") != "success"
                or type(verification.get("row_count")) is not int
                or verification["row_count"] != expected):
            raise _Stop("needs_human", "Rerun did not pass the job status and expected-row-count checks")

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
    except _Stop as stop:
        result["outcome"], result["reason"] = stop.outcome, stop.reason
    except Exception as exc:
        result["outcome"] = "needs_human" if stage in ("approval", "apply", "verification", "memory") else "gave_up"
        result["reason"] = f"{stage} failed ({type(exc).__name__}); no automatic retry"
    record("incident", "terminal", result["outcome"])
    return result
