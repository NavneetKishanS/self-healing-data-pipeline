"""
Dev C owns this file.

The simplest possible human-approval UI: a Flask page showing the diagnosis + proposed
fix + confidence, with Approve/Reject buttons. request_approval() blocks by polling an
in-memory decision variable until the human clicks a button, or a timeout is hit so a
stuck browser tab can never hang the demo.

Run standalone to test:  python -m memory_approval.approval_server
"""

import time
import threading
from flask import Flask, request, render_template_string

app = Flask(__name__)

_pending = {"diagnosis": None, "proposed_fix": None, "confidence": None}
_decision = {"value": None, "note": ""}

TEMPLATE = """
<!doctype html>
<title>Approve fix</title>
<div style="font-family: sans-serif; max-width: 640px; margin: 40px auto;">
  <h2>Pipeline fix pending approval</h2>
  <p><b>Diagnosis:</b> {{ diagnosis }}</p>
  <p><b>Proposed fix:</b> <code>{{ proposed_fix }}</code></p>
  <p><b>Confidence:</b> {{ confidence }}</p>
  <form method="post" action="/decide">
    <input type="hidden" name="note" value="">
    <button name="decision" value="approve" style="padding:10px 20px; margin-right:10px;">Approve</button>
    <button name="decision" value="reject" style="padding:10px 20px;">Reject</button>
  </form>
</div>
"""


@app.route("/")
def show():
    return render_template_string(
        TEMPLATE,
        diagnosis=_pending["diagnosis"],
        proposed_fix=_pending["proposed_fix"],
        confidence=_pending["confidence"],
    )


@app.route("/decide", methods=["POST"])
def decide():
    _decision["value"] = request.form["decision"] == "approve"
    _decision["note"] = request.form.get("note", "")
    return "Recorded. You can close this tab."


def request_approval(diagnosis: str, proposed_fix: dict, confidence: float, timeout_s: int = 120) -> dict:
    """
    BLOCKING. Sets the pending state for the web page, then polls until a decision is
    made or the timeout elapses (default reject on timeout — never hang the demo).
    """
    _pending.update({"diagnosis": diagnosis, "proposed_fix": proposed_fix, "confidence": confidence})
    _decision.update({"value": None, "note": ""})

    print(f"[approval] Waiting for human decision at http://localhost:5050/  (timeout {timeout_s}s)")
    start = time.time()
    while _decision["value"] is None:
        if time.time() - start > timeout_s:
            return {"approved": False, "human_note": "Timed out waiting for approval."}
        time.sleep(0.5)

    return {"approved": _decision["value"], "human_note": _decision["note"]}


def run_server_in_background(port: int = 5050):
    thread = threading.Thread(target=lambda: app.run(port=port, debug=False, use_reloader=False))
    thread.daemon = True
    thread.start()


if __name__ == "__main__":
    app.run(port=5050, debug=True)
