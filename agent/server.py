"""Authenticated REST and AG-UI adapters; no changes to Dev C's frontend or Flask app."""

from copy import deepcopy
import json
from pathlib import Path
from queue import Empty, Queue
import re
import sqlite3
from threading import Event, Lock, Thread
from time import monotonic

from ag_ui.core import EventType, RunAgentInput, RunStartedEvent, RunFinishedEvent, StateSnapshotEvent
from ag_ui.encoder import EventEncoder
from flask import Flask, Response, jsonify, request, stream_with_context

from .auth import Auth0Verifier, Unauthorized, Forbidden
from .live import run_live
from .dataset import OrdersDataset
from .iris import IrisDataset, seed_iris, MEASUREMENTS
import random
from uuid import uuid4
from datetime import datetime, timezone
from .settings import integration_status, positive_number
from .validation import fingerprint


def create_app(*, local_no_auth=False, runner=run_live, verifier=None, state_dir=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
    auth = None if local_no_auth else (verifier or Auth0Verifier())
    directory = Path(state_dir) if state_dir else Path(__file__).parent / ".state"
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "runs.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, owner TEXT, status TEXT, result TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS ingestion (id TEXT, owner TEXT, payload TEXT, result TEXT, created TEXT, run_id TEXT, PRIMARY KEY(id, owner))")
        seed_iris(db)
    runs, lock = {}, Lock()
    ingestion_lock = Lock()

    def identity(permission):
        if local_no_auth:
            # The CLI binds to loopback. Reject browser cross-origin requests in local mode.
            origin = request.headers.get("Origin")
            if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
                raise Forbidden("Local mode only accepts same-origin browser requests")
            if request.remote_addr not in ("127.0.0.1", "::1", None):
                raise Forbidden("Local mode requires loopback")
            return "local-demo"
        return auth.verify(request.headers.get("Authorization", ""), permission)

    @app.errorhandler(Unauthorized)
    def unauthorized(error):
        return jsonify(error=str(error)), 401

    @app.errorhandler(Forbidden)
    def forbidden(error):
        return jsonify(error=str(error)), 403

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    def snapshot(run):
        return {"incident_id": run["id"], "status": run["status"],
                "approval": deepcopy(run["pending"]), "result": deepcopy(run["result"])}

    def owned(run_id, owner):
        run = runs.get(run_id)
        if run and run["owner"] == owner:
            return run
        raise Forbidden("Incident not available to this user")

    def start(run_id, owner, job_id, inject_failure, rows=None, dataset_name="orders"):
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
            raise ValueError("runId must contain 1–128 letters, numbers, underscores or hyphens")
        # Current repo fixture has exactly one job. Broader access needs a real job/workspace ACL.
        if job_id != "job_1" or inject_failure not in (None, "schema_drift"):
            raise ValueError("This demo adapter supports job_1 and optional schema_drift injection")
        with lock:
            if run_id in runs:
                return owned(run_id, owner)
            if any(r["status"] != "finished" for r in runs.values()):
                raise ValueError("Another incident is active; shared pipeline state is serialized")
            if len(runs) >= 100:
                raise ValueError("Demo run limit reached; restart the server")
            with sqlite3.connect(database) as db:
                try:
                    db.execute("INSERT INTO runs VALUES (?, ?, 'started', NULL)", (run_id, owner))
                except sqlite3.IntegrityError:
                    raise ValueError("This run ID was already used; inspect stored outcome before starting another incident") from None
            run = {"id": run_id, "owner": owner, "status": "running", "pending": None,
                   "result": None, "decision": None, "event": Event(), "queue": Queue(),
                   "streaming": False, "deadline": None}
            runs[run_id] = run

        def approval(*, diagnosis, proposed_fix, confidence):
            with lock:
                run["pending"] = {"diagnosis": diagnosis, "proposed_fix": deepcopy(proposed_fix),
                                  "confidence": confidence, "fix_hash": fingerprint(proposed_fix),
                                  "incident_id": run_id}
                run["status"] = "awaiting_approval"
                timeout = positive_number("APPROVAL_TIMEOUT_SECONDS", 120)
                run["deadline"] = monotonic() + timeout
                run["queue"].put(snapshot(run))
            run["event"].wait(timeout)
            with lock:
                decision = run["decision"] or {"approved": False, "human_note": "Approval expired"}
                run["pending"] = None
                run["status"] = "running"
                return deepcopy(decision)

        def worker():
            try:
                kwargs = {} if rows is None else {"rows": deepcopy(rows), "dataset_name": dataset_name}
                result = runner(job_id, approval=approval, incident_id=run_id, inject_failure=inject_failure, **kwargs)
                result["approval_subject"] = run["owner"] if run["decision"] else None
            except Exception as exc:
                result = {"incident_id": run_id, "job_id": job_id, "outcome": "gave_up",
                          "reason": "Live run failed (" + type(exc).__name__ + "); no automatic retry"}
            with lock:
                run["result"] = result
                run["status"] = "finished"
                with sqlite3.connect(database) as db:
                    db.execute("UPDATE runs SET status='finished', result=? WHERE id=?", (json.dumps(result), run_id))
                run["queue"].put(snapshot(run))
        Thread(target=worker, daemon=True).start()
        return run

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "local-no-auth" if local_no_auth else "auth0"}

    @app.get("/api/integrations")
    def integrations():
        identity("read:incidents")
        return integration_status()

    @app.get("/api/session")
    def session():
        return {"sub": identity("read:incidents")}

    @app.post("/api/ingestion")
    def ingest():
        owner = identity("run:incidents")
        data = request.get_json()
        return ingest_data(owner, data)

    def ingest_data(owner, data, replay=None):
        if not isinstance(data, dict):
            raise ValueError("Expected batchId and rows")
        batch_id, rows = data.get("batchId"), data.get("rows")
        if not isinstance(batch_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", batch_id):
            raise ValueError("batchId must contain 1–128 letters, numbers, underscores or hyphens")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100 or not all(isinstance(row, dict) for row in rows):
            raise ValueError("Send 1–100 row objects per batch")
        payload = json.dumps(rows, allow_nan=False, sort_keys=True)
        dataset_name = data.get("dataset", "orders")
        if dataset_name not in ("orders", "iris"):
            raise ValueError("Unknown dataset")
        payload = json.dumps({"rows": rows, "dataset": dataset_name}, allow_nan=False, sort_keys=True)
        dataset = IrisDataset(rows) if dataset_name == "iris" else OrdersDataset(rows=rows)
        errors = dataset.errors()
        flagged = sorted({error["row"] for error in errors})
        result = {"dataset": dataset_name, "replay": replay, "batch_id": batch_id, "status": "schema_drift" if errors else "valid",
                  "row_count": len(rows), "flagged_count": len(flagged), "mismatches": errors,
                  "flagged_samples": [{"row": index, "data": rows[index - 1]} for index in flagged],
                  "created_at": datetime.now(timezone.utc).isoformat()}
        with lock, sqlite3.connect(database) as db:
            existing = db.execute("SELECT payload,result FROM ingestion WHERE id=? AND owner=?", (batch_id, owner)).fetchone()
            if existing:
                if existing[0] != payload:
                    return jsonify(error="batchId already used for different rows"), 409
                return json.loads(existing[1])
            count = db.execute("SELECT COUNT(*) FROM ingestion WHERE owner=?", (owner,)).fetchone()[0]
            if count >= 1000:
                return jsonify(error="Local ingestion storage limit reached (1000 batches)"), 429
            db.execute("INSERT INTO ingestion VALUES (?, ?, ?, ?, ?, NULL)",
                       (batch_id, owner, payload, json.dumps(result), result["created_at"]))
        return jsonify(result), 201

    @app.post("/api/ingestion/replay")
    def replay_iris():
        owner = identity("run:incidents")
        data = request.get_json()
        if not isinstance(data, dict) or type(data.get("corrupt")) is not bool:
            raise ValueError("corrupt must be a boolean")
        with sqlite3.connect(database) as db:
            original = json.loads(db.execute("SELECT row_json FROM iris_source ORDER BY RANDOM() LIMIT 1").fetchone()[0])
        incoming = deepcopy(original)
        column = random.choice(MEASUREMENTS) if data["corrupt"] else None
        if column:
            incoming[column] = str(incoming[column])
        return ingest_data(owner, {"batchId": str(uuid4()), "rows": [incoming], "dataset": "iris"},
                           replay={"source": "Kaggle uciml/iris", "original": original, "changed_column": column})

    @app.get("/api/ingestion")
    def ingestion_status():
        owner = identity("read:incidents")
        with sqlite3.connect(database) as db:
            records = db.execute("SELECT result,run_id FROM ingestion WHERE owner=? ORDER BY created DESC LIMIT 30", (owner,)).fetchall()
            total = db.execute("SELECT COUNT(*) FROM ingestion WHERE owner=?", (owner,)).fetchone()[0]
        return {"batches": [{**json.loads(row[0]), "run_id": row[1]} for row in records], "total_batches": total}

    @app.post("/api/ingestion/<batch_id>/investigate")
    def investigate_batch(batch_id):
        with ingestion_lock:
            return start_batch_investigation(batch_id)

    def start_batch_investigation(batch_id):
        owner = identity("run:incidents")
        with sqlite3.connect(database) as db:
            row = db.execute("SELECT payload,result,run_id FROM ingestion WHERE id=? AND owner=?", (batch_id, owner)).fetchone()
            if not row:
                raise Forbidden("Batch not available to this user")
            if json.loads(row[1])["status"] != "schema_drift":
                raise ValueError("Batch has no schema drift")
            run_id = row[2] or str(uuid4())
            # Claim once before starting. A restart never replays uncertain work.
            if not row[2]:
                db.execute("UPDATE ingestion SET run_id=? WHERE id=? AND owner=? AND run_id IS NULL", (run_id, batch_id, owner))
                db.commit()
        if row[2]:
            return {"incident_id": run_id}
        try:
            stored = json.loads(row[0])
            rows = stored["rows"] if isinstance(stored, dict) else stored
            name = stored.get("dataset", "orders") if isinstance(stored, dict) else "orders"
            run = start(run_id, owner, "job_1", None, rows=rows, dataset_name=name)
        except ValueError:
            with sqlite3.connect(database) as db:
                exists = db.execute("SELECT id FROM runs WHERE id=?", (run_id,)).fetchone()
                if not exists:
                    db.execute("UPDATE ingestion SET run_id=NULL WHERE id=? AND owner=?", (batch_id, owner))
            raise
        return {"incident_id": run["id"]}

    @app.post("/api/runs")
    def create_run():
        owner = identity("run:incidents")
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        run = start(data.get("runId"), owner, data.get("job_id", "job_1"), data.get("inject_failure"))
        with lock:
            return jsonify(snapshot(run)), 202

    @app.get("/api/runs/<run_id>")
    def read_run(run_id):
        owner = identity("read:incidents")
        with lock:
            if run_id in runs:
                return snapshot(owned(run_id, owner))
        with sqlite3.connect(database) as db:
            row = db.execute("SELECT status,result FROM runs WHERE id=? AND owner=?", (run_id, owner)).fetchone()
        if not row:
            raise Forbidden("Incident not available to this user")
        return {"incident_id": run_id, "status": row[0], "result": json.loads(row[1]) if row[1] else None,
                "warning": "Restarted process; pending work is not resumed automatically"}

    @app.post("/api/runs/<run_id>/decision")
    def decide(run_id):
        owner = identity("approve:fixes")
        data = request.get_json()
        if not isinstance(data, dict) or type(data.get("approved")) is not bool:
            raise ValueError("approved must be a JSON boolean")
        note = data.get("human_note", "")
        if not isinstance(note, str) or len(note) > 1000:
            raise ValueError("human_note must be text of at most 1000 characters")
        with lock:
            run = owned(run_id, owner)
            if (run["status"] != "awaiting_approval" or run["decision"] is not None
                    or monotonic() >= run["deadline"]):
                return jsonify(error="No current undecided approval"), 409
            if data.get("fix_hash") != run["pending"]["fix_hash"]:
                return jsonify(error="Stale or changed proposal"), 409
            run["decision"] = {"approved": data["approved"], "human_note": note, "subject": owner}
            run["event"].set()
        return {"recorded": True}

    @app.post("/agent")
    def ag_ui():
        owner = identity("run:incidents")
        try:
            data = RunAgentInput.model_validate(request.get_json())
        except Exception:
            raise ValueError("Invalid AG-UI RunAgentInput") from None
        props = data.forwarded_props or {}
        if not isinstance(props, dict):
            raise ValueError("forwardedProps must be an object")
        run = start(data.run_id, owner, props.get("job_id", "job_1"), props.get("inject_failure"))
        with lock:
            if run["streaming"]:
                return jsonify(error="A stream is already attached; use GET /api/runs/<id>"), 409
            run["streaming"] = True
        encoder = EventEncoder()

        @stream_with_context
        def events():
            try:
                yield encoder.encode(RunStartedEvent(type=EventType.RUN_STARTED, thread_id=data.thread_id, run_id=data.run_id))
                with lock:
                    current = snapshot(run)
                while True:
                    yield encoder.encode(StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=current))
                    if current["status"] == "finished":
                        break
                    try:
                        current = run["queue"].get(timeout=10)
                    except Empty:
                        yield ": heartbeat\n\n"
                        with lock:
                            current = snapshot(run)
                yield encoder.encode(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=data.thread_id, run_id=data.run_id))
            finally:
                with lock:
                    run["streaming"] = False
        return Response(events(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
