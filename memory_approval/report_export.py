"""Optional one-shot, redacted terminal-incident export to Ambiguous Docs."""
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from memory_approval.storage import runtime_path, transaction
from memory_approval.validation import nonempty

DEFAULT_URL = "https://app.ambiguous.ai/api/documents"


def _bounded_text(value, label, limit=2000, default="Not recorded"):
    if value is None:
        return default
    return nonempty(value, label, limit).strip()


def _safe_sources(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("sources must be a list")
    result = []
    for source in value[:3]:
        if not isinstance(source, dict):
            raise ValueError("each source must be an object")
        title = _bounded_text(source.get("title"), "source title", 500)
        url = _bounded_text(source.get("url"), "source URL", 2000)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("source URL must be http or https")
        excerpt = source.get("excerpt")
        result.append({"title": title, "url": url,
                       "excerpt": _bounded_text(excerpt, "source excerpt", 1000, "") if excerpt else ""})
    return result


def _report_fields(incident):
    if not isinstance(incident, dict):
        raise ValueError("incident must be an object")
    incident_id = _bounded_text(incident.get("incident_id"), "incident_id", 200)
    diagnosis = _bounded_text(incident.get("diagnosis", incident.get("root_cause")), "diagnosis")
    decision = _bounded_text(incident.get("approval_decision"), "approval_decision", 100)
    outcome = _bounded_text(incident.get("observed_outcome", incident.get("outcome")), "observed_outcome", 100)
    if decision not in ("approved", "rejected", "timed_out", "not_requested"):
        raise ValueError("Unsupported approval_decision")
    if outcome not in ("fixed", "needs_human", "gave_up"):
        raise ValueError("Unsupported observed_outcome")
    return incident_id, diagnosis, decision, outcome, _safe_sources(incident.get("sources"))


def _document(incident_id, diagnosis, decision, outcome, sources):
    blocks = [
        {"type": "heading", "level": 1, "text": f"Incident report: {incident_id}"},
        {"type": "paragraph", "text": f"Diagnosis: {diagnosis}"},
        {"type": "paragraph", "text": f"Approval decision: {decision}"},
        {"type": "paragraph", "text": f"Observed outcome: {outcome}"},
    ]
    if sources:
        blocks.append({"type": "heading", "level": 2, "text": "External evidence"})
        for source in sources:
            detail = f"{source['title']} — {source['url']}"
            if source["excerpt"]:
                detail += f" — {source['excerpt']}"
            blocks.append({"type": "paragraph", "text": detail})
    return {"type": "doc", "title": f"Pipeline incident {incident_id}", "content": blocks}


def _reserve_attempt(incident_id):
    with transaction(runtime_path("report_exports.json"), dict) as records:
        if incident_id in records:
            return False, records[incident_id].copy()
        record = {"status": "attempting", "document_id": None, "document_url": None,
                  "attempted_at": time.time(), "error": None}
        records[incident_id] = record
        return True, record.copy()


def _finish_attempt(incident_id, **values):
    with transaction(runtime_path("report_exports.json"), dict) as records:
        records[incident_id].update(values)
        return records[incident_id].copy()


def report_export(incident: dict) -> dict:
    """Attempt one export per incident; failures never affect incident resolution."""
    api_key = os.environ.get("AMBIGUOUS_API_KEY", "").strip()
    if not api_key:
        return {"exported": False, "document_id": None, "document_url": None,
                "error": "AMBIGUOUS_API_KEY is not configured"}

    try:
        incident_id, diagnosis, decision, outcome, sources = _report_fields(incident)
        reserved, existing = _reserve_attempt(incident_id)
        if not reserved:
            return {"exported": existing["status"] == "exported",
                    "document_id": existing.get("document_id"),
                    "document_url": existing.get("document_url"),
                    "error": existing.get("error") or (None if existing["status"] == "exported"
                                                         else "Export was already attempted")}

        api_url = os.environ.get("AMBIGUOUS_API_URL", DEFAULT_URL).strip()
        parsed = urlparse(api_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("AMBIGUOUS_API_URL must be an HTTPS URL")
        timeout = float(os.environ.get("AMBIGUOUS_TIMEOUT_SECONDS", "8"))
        if not 0 < timeout <= 30:
            raise ValueError("AMBIGUOUS_TIMEOUT_SECONDS must be between 0 and 30")

        body = json.dumps(_document(incident_id, diagnosis, decision, outcome, sources)).encode()
        request = Request(api_url, data=body, method="POST", headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        document_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(document_id, str) or not document_id:
            raise ValueError("Ambiguous response did not contain a document ID")
        document_url = f"https://app.ambiguous.ai/docs/{document_id}"
        result = _finish_attempt(incident_id, status="exported", document_id=document_id,
                                 document_url=document_url, error=None)
        return {"exported": True, "document_id": result["document_id"],
                "document_url": result["document_url"], "error": None}
    except (ValueError, TypeError, OSError, HTTPError, URLError, json.JSONDecodeError) as error:
        message = f"{type(error).__name__}: export failed"
        if "incident_id" in locals() and runtime_path("report_exports.json").exists():
            try:
                _finish_attempt(incident_id, status="failed", error=message)
            except (KeyError, ValueError, OSError):
                pass
        return {"exported": False, "document_id": None, "document_url": None, "error": message}
