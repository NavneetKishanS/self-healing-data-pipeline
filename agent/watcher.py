"""Always-on incident detection: investigate flagged ingestion batches without a human click.

The watcher is the server's "Investigate flagged batch" button on a timer. It costs nothing while
idle (no model calls, one SQLite query per tick), starts at most one incident at a time through the
same claim-and-start path the button uses, retries only incidents that ended before any model
answered, and stops starting incidents once the day's model-request budget is spent.
"""

from datetime import datetime, timezone
import json
import sqlite3
from threading import Event, Thread
import time

from .model_client import TRANSIENT_ERRORS
from .workflow import MAX_MODEL_CALLS

RETRY_BACKOFF_SECONDS = (60, 300)          # wait before attempt 2 and attempt 3; no fourth attempt
MAX_ATTEMPTS = len(RETRY_BACKOFF_SECONDS) + 1
# Worth trying again later. A missing model is a configuration problem once every fallback lacks it.
RETRY_LATER = TRANSIENT_ERRORS - {"NotFoundError"}


def _retryable(result):
    """True only when the run produced no model answer and touched nothing, and the last provider
    error was transient: replaying is safe and may succeed. Anything after apply, any human decision,
    and any configuration error is final."""
    if not (isinstance(result, dict) and result.get("terminal_event") == "model_error"
            and result.get("application") is None and result.get("mutation_state") == "not_attempted"):
        return False
    requests = result.get("model_requests")
    last = requests[-1] if isinstance(requests, list) and requests else None
    return isinstance(last, dict) and last.get("error_type") in RETRY_LATER


class Watcher:
    def __init__(self, database, *, investigate, busy, interval=30.0, budget=40, backoff=RETRY_BACKOFF_SECONDS):
        self.database, self.investigate, self.busy = database, investigate, busy
        self.interval, self.budget, self.backoff = float(interval), int(budget), tuple(backoff)
        self.wakeup, self.stopped, self.thread, self.last = Event(), Event(), None, None
        self.investigations = 0                # started by this process, for the status endpoint
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS watch_attempts (batch_id TEXT, owner TEXT, attempts INTEGER NOT NULL, "
                       "next_at REAL NOT NULL, PRIMARY KEY(batch_id, owner))")
            db.execute("CREATE TABLE IF NOT EXISTS model_budget (day TEXT PRIMARY KEY, used INTEGER NOT NULL)")

    @staticmethod
    def today():
        return datetime.now(timezone.utc).date().isoformat()

    def used_today(self):
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT used FROM model_budget WHERE day=?", (self.today(),)).fetchone()
        return row[0] if row else 0

    def charge(self, result):
        """Charge a finished run's provider requests (every attempt, including rate-limited ones) to
        today's budget. Called for every run, watcher-started or not."""
        requests = result.get("model_requests") if isinstance(result, dict) else None
        count = len(requests) if isinstance(requests, list) else 0
        if count:
            with sqlite3.connect(self.database) as db:
                db.execute("INSERT INTO model_budget VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET used = used + excluded.used",
                           (self.today(), count))

    def wake(self):
        self.wakeup.set()

    def candidates(self, now):
        """Oldest first: unclaimed flagged batches, then finished claims that are safe to replay."""
        with sqlite3.connect(self.database) as db:
            fresh = db.execute("SELECT id, owner, result FROM ingestion WHERE run_id IS NULL ORDER BY created").fetchall()
            claimed = db.execute(
                "SELECT i.id, i.owner, r.result, w.attempts, w.next_at FROM ingestion i "
                "JOIN runs r ON r.id = i.run_id LEFT JOIN watch_attempts w ON w.batch_id = i.id AND w.owner = i.owner "
                "WHERE r.status = 'finished' ORDER BY i.created").fetchall()
        for batch_id, owner, stored in fresh:
            if json.loads(stored).get("status") == "schema_drift":
                yield batch_id, owner, 1
        for batch_id, owner, stored, attempts, next_at in claimed:
            attempts = attempts or 1                       # a click, not the watcher, made the first attempt
            if attempts < MAX_ATTEMPTS and (next_at or 0) <= now and _retryable(json.loads(stored) if stored else None):
                yield batch_id, owner, attempts + 1

    def tick(self, now=None):
        """One pass. Returns what happened so the status endpoint and tests can see it."""
        now = time.time() if now is None else now
        if self.busy():
            return {"state": "busy"}
        used = self.used_today()
        if used + MAX_MODEL_CALLS > self.budget:
            return {"state": "budget_exhausted", "used_today": used, "daily_limit": self.budget}
        for batch_id, owner, attempt in self.candidates(now):
            try:
                run_id = self.investigate(batch_id, owner, again=attempt > 1)["incident_id"]
            except ValueError as exc:
                return {"state": "skipped", "batch_id": batch_id, "reason": str(exc)}
            wait = self.backoff[attempt - 1] if attempt - 1 < len(self.backoff) else 0
            with sqlite3.connect(self.database) as db:
                db.execute("INSERT OR REPLACE INTO watch_attempts VALUES (?, ?, ?, ?)", (batch_id, owner, attempt, now + wait))
            self.investigations += 1
            return {"state": "started", "batch_id": batch_id, "run_id": run_id, "attempt": attempt}
        return {"state": "idle"}

    def run(self):
        while not self.stopped.is_set():
            try:
                self.last = self.tick()
            except Exception as exc:
                # Never let a storage hiccup kill the thread; the next tick retries.
                self.last = {"state": "error", "error_type": type(exc).__name__}
            if self.last["state"] in ("started", "error"):
                print("[watcher] " + json.dumps(self.last), flush=True)
            self.wakeup.wait(self.interval)
            self.wakeup.clear()

    def start(self):
        if self.thread is None:
            self.thread = Thread(target=self.run, daemon=True, name="agent-watcher")
            self.thread.start()
        return self

    def stop(self):
        self.stopped.set()
        self.wake()
        if self.thread is not None:
            self.thread.join(timeout=5)

    def status(self):
        return {"running": bool(self.thread and self.thread.is_alive()), "interval_seconds": self.interval,
                "investigations": self.investigations,
                "budget": {"day": self.today(), "used_today": self.used_today(), "daily_limit": self.budget,
                           "per_incident_reserve": MAX_MODEL_CALLS},
                "retry": {"max_attempts": MAX_ATTEMPTS, "backoff_seconds": list(self.backoff)},
                "last": self.last}
