"""Narrow Ambiguous document exporter, isolated from Dev C's modules."""

import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

import httpx

from .settings import configured, positive_number


def export_report(result: dict, *, state_dir=None) -> dict:
    if not configured("AMBIGUOUS_API_KEY"):
        return {"status": "unavailable", "error": "missing AMBIGUOUS_API_KEY", "url": None}
    incident_id = result["incident_id"]
    directory = Path(state_dir) if state_dir else Path(__file__).parent / ".state"
    directory.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(directory / "exports.sqlite") as db:
        db.execute("CREATE TABLE IF NOT EXISTS exports (incident_id TEXT PRIMARY KEY, status TEXT, url TEXT)")
        # Claim before the request. A timeout or crash may mean the remote create succeeded.
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT status, url FROM exports WHERE incident_id=?", (incident_id,)).fetchone()
        if existing:
            return {"status": existing[0], "url": existing[1],
                    "error": None if existing[0] == "sent" else "Export already attempted; verify remotely before retrying"}
        db.execute("INSERT INTO exports VALUES (?, 'uncertain', NULL)", (incident_id,))
        db.commit()
        summary = {
            "incident_id": incident_id, "outcome": result["outcome"], "reason": result["reason"],
            "diagnosis": result.get("diagnosis"), "critique": result.get("critique"),
            "approval": result.get("approval"), "verification": result.get("verification"),
            "sources": [{"title": s["title"], "url": s["url"]} for s in result.get("sources", [])],
            "warnings": result.get("warnings", []),
        }
        content = json.dumps(summary, indent=2)
        for name, secret in os.environ.items():
            if any(marker in name for marker in ("API_KEY", "TOKEN", "SECRET")) and len(secret) >= 8:
                content = content.replace(secret, "[REDACTED]")
        content = re.sub(r"(?i)Bearer\s+\S+|sk-[\w-]{12,}", "[REDACTED]", content)
        try:
            response = httpx.post(
                "https://app.ambiguous.ai/api/documents",
                headers={"Authorization": "Bearer " + os.environ["AMBIGUOUS_API_KEY"]},
                json={"type": "doc", "title": "Pipeline incident " + incident_id,
                      "content": [{"type": "heading", "level": 1, "text": "Pipeline incident report"},
                                  {"type": "paragraph", "text": content}]},
                timeout=positive_number("AMBIGUOUS_TIMEOUT_SECONDS", 15), follow_redirects=False,
            )
            response.raise_for_status()
            document_id = response.json().get("id")
            if not isinstance(document_id, str) or not document_id:
                raise ValueError("Document response lacks an id")
            url = "https://app.ambiguous.ai/docs/" + quote(document_id, safe="")
            db.execute("UPDATE exports SET status='sent', url=? WHERE incident_id=?", (url, incident_id))
            return {"status": "sent", "url": url, "error": None}
        except Exception as exc:
            code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
            return {"status": "uncertain", "url": None,
                    "error": f"Ambiguous export not confirmed ({code}); no automatic retry"}
