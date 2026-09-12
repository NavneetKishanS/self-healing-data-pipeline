"""
Dev C owns this file.

The simplest possible human-approval UI: a Flask page showing the diagnosis + proposed
fix + confidence, with Approve/Reject buttons. request_approval() blocks by polling an
in-memory decision variable until the human clicks a button, or a timeout is hit so a
stuck browser tab can never hang the demo.

Run standalone to test:  python -m memory_approval.approval_server
"""

import json
import os
import threading
import time

from flask import Flask, g, jsonify, render_template, request
from werkzeug.serving import make_server
from memory_approval.approvals import create_approval, decide_approval, get_approval, list_pending
from memory_approval.validation import fix_hash

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64000


@app.before_request
def authorize():
    # Local demo by default. Auth0 mode protects ALL approval transports, including Flask.
    if os.environ.get("APPROVAL_AUTH_MODE", "local") == "auth0":
        from memory_approval.auth import verify_request
        permission = "approve:fixes" if request.path in ("/decide", "/api/decide", "/ag-ui") else "read:incidents"
        subject, error, status = verify_request(request.headers.get("Authorization", ""), permission)
        if error:
            return jsonify(error=error), status
        g.subject = subject
    elif os.environ.get("APPROVAL_AUTH_MODE", "local") == "local":
        g.subject = "local-operator"
    else:
        return jsonify(error="Invalid APPROVAL_AUTH_MODE"), 503


@app.after_request
def response_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.errorhandler(ValueError)
def invalid_input(error):
    return jsonify(error=str(error)), 400


@app.route("/")
def show():
    return render_template("approval.html", approvals=list_pending())


@app.get("/api/pending")
def pending():
    return jsonify(approvals=list_pending())


@app.post("/decide")
@app.post("/api/decide")
def decide():
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    if not decide_approval(payload, g.subject):
        return jsonify(error="Approval is stale, expired, already decided, or does not match"), 409
    if request.is_json:
        return jsonify(recorded=True)
    return 'Decision recorded. <a href="/">Return to pending approvals</a>'


@app.post("/ag-ui")
def ag_ui():
    """A bounded AG-UI snapshot/decision bridge; never starts an LLM or a repair.

    forwardedProps.decision is validated against the persisted proposal. Agent state,
    tool messages, and proposed fixes supplied by the browser are never authoritative.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("Expected an AG-UI request object")
    thread_id, run_id = body.get("threadId"), body.get("runId")
    if not all(isinstance(v, str) and 0 < len(v) <= 200 for v in (thread_id, run_id)):
        raise ValueError("threadId and runId are required")
    props = body.get("forwardedProps", {})
    if not isinstance(props, dict):
        raise ValueError("forwardedProps must be an object")
    decision = props.get("decision")
    result = None
    if decision is not None:
        if not decide_approval(decision, g.subject):
            return jsonify(error="Decision expired, already processed, or mismatched"), 409
        result = {"recorded": True, "decision": decision["decision"]}
    events = [
        {"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id},
        {"type": "STATE_SNAPSHOT", "snapshot": {"approvals": list_pending(), "result": result}},
        {"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id},
    ]
    return app.response_class("".join("data: " + json.dumps(e) + "\n\n" for e in events),
                              mimetype="text/event-stream")


def _approval_timeout():
    try:
        timeout = float(os.environ.get("APPROVAL_TIMEOUT_SECONDS", "120"))
    except ValueError as error:
        raise ValueError("APPROVAL_TIMEOUT_SECONDS must be a number") from error
    if not 0 < timeout <= 1800:
        raise ValueError("APPROVAL_TIMEOUT_SECONDS must be between 0 and 1800")
    return timeout


def request_approval(diagnosis: str, proposed_fix: dict, confidence: float) -> dict:
    """Block for an exact human decision; public signature matches CONTEXT.md."""
    record = create_approval(diagnosis, proposed_fix, confidence, _approval_timeout())
    port = os.environ.get("APPROVAL_SERVER_PORT", "5050")
    print(f"[approval] Review {record['incident_id']} at http://localhost:{port}/")
    while True:
        current = get_approval(record["approval_id"])
        if current is None:
            return {"approved": False, "human_note": "Approval record became unavailable."}
        if current["status"] != "pending":
            approved = current["status"] == "approved"
            # Detect mutation of the original Python object while the human was deciding.
            try:
                approved = approved and fix_hash(proposed_fix) == current["fix_hash"]
            except (ValueError, TypeError):
                approved = False
            decision = {"approved": approved, "human_note": current["human_note"]}
            if current["status"] == "expired":
                decision["expired"] = True   # nobody decided; the workflow must not learn from it
            return decision
        time.sleep(min(0.1, max(0.001, current["expires_at"] - time.time())))


def run_server_in_background(port: int = 5050):
    # Bind before starting the thread: port conflicts fail visibly in the caller.
    effective_port = int(os.environ.get("APPROVAL_SERVER_PORT", port))
    server = make_server("127.0.0.1", effective_port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    app.run(host="127.0.0.1", port=int(os.environ.get("APPROVAL_SERVER_PORT", 5050)),
            debug=False, use_reloader=False)
