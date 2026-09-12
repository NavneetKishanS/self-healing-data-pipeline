"""Small Slack adapter for signed human-in-the-loop approval messages."""

import hashlib
import hmac
import json
import os
import time
from threading import Lock

import httpx

from .settings import configured, positive_number


class SlackNotifier:
    def __init__(self, token=None, channel=None, signing_secret=None, post=httpx.post):
        self.token = token or os.getenv("SLACK_BOT_TOKEN", "").strip()
        self.channel = channel or os.getenv("SLACK_CHANNEL_ID", "").strip()
        self.signing_secret = signing_secret or os.getenv("SLACK_SIGNING_SECRET", "").strip()
        self._post = post
        self.team = os.getenv("SLACK_TEAM_ID", "").strip()
        self.app_id = os.getenv("SLACK_APP_ID", "").strip()
        self.reviewers = set(filter(None, os.getenv("SLACK_APPROVER_IDS", "").replace(" ", "").split(",")))
        self._alert_lock = Lock()
        self._last_alert = 0

    @property
    def enabled(self):
        return all((self.token, self.channel, self.team, self.app_id, self.reviewers))

    def authorized(self, payload):
        return (self.enabled and payload.get("team", {}).get("id") == self.team
                and payload.get("api_app_id") == self.app_id
                and payload.get("channel", {}).get("id") == self.channel
                and payload.get("user", {}).get("id") in self.reviewers)

    def send(self, method, payload):
        try:
            response = self._post("https://slack.com/api/" + method,
                                  headers={"Authorization": "Bearer " + self.token},
                                  json=payload, timeout=positive_number("SLACK_TIMEOUT_SECONDS", 5))
            data = response.json()
            if response.is_success and isinstance(data, dict) and data.get("ok") is True:
                return {"status": "sent", "channel": data.get("channel"), "timestamp": data.get("ts")}
        except (httpx.HTTPError, ValueError):
            pass
        return {"status": "unavailable", "error": "Slack request failed"}

    def post_detection(self, batch):
        if not self.enabled:
            return
        with self._alert_lock:
            now = time.monotonic()
            if now - self._last_alert < 30:
                return
            self._last_alert = now
        # Limit replay alerts to one per 30 seconds; never send raw dataset rows.
        return self.send("chat.postMessage", {"channel": self.channel,
            "text": f"Schema drift detected in {batch['dataset']}: {batch['flagged_count']} flagged row(s). "
                    f"Batch: {batch['batch_id']}. Open the dashboard to investigate. "
                    "Replay alerts are limited to one per 30 seconds.",
            "unfurl_links": False, "unfurl_media": False})

    def post_result(self, result, message):
        if not self.enabled:
            return
        text = (f"Incident {result['incident_id']} — {result.get('outcome', 'unknown')}. "
                f"{result.get('reason', '')}" )[:2900]
        payload = {"channel": self.channel, "text": text,
                   "blocks": [{"type": "section", "text": {"type": "plain_text", "text": text}}]}
        if message and message.get("status") == "sent":
            payload["ts"] = message["timestamp"]
            return self.send("chat.update", payload)
        return self.send("chat.postMessage", payload)

    def post_approval(self, approval):
        """Post one approval request. Failure is reported but never changes workflow state."""
        if not self.enabled:
            return {"status": "disabled"}
        value = json.dumps({"incident_id": approval["incident_id"], "fix_hash": approval["fix_hash"]},
                           separators=(",", ":"))
        fix = json.dumps(approval["proposed_fix"], indent=2, sort_keys=True)
        if len(fix) > 2500:
            fix = fix[:2497] + "..."
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "Pipeline repair needs approval"}},
            {"type": "section", "text": {"type": "plain_text", "text":
                (f"Incident: {approval['incident_id']}\nDiagnosis: {approval['diagnosis']}\n"
                f"Confidence: {round(approval['confidence'] * 100)}% (model estimate)")[:2900]}},
            {"type": "section", "text": {"type": "plain_text", "text": f"Proposed repair\n{fix}"}},
            {"type": "actions", "block_id": "pipeline_approval", "elements": [
                {"type": "button", "action_id": "pipeline_approve", "style": "primary",
                 "text": {"type": "plain_text", "text": "Approve"}, "value": value},
                {"type": "button", "action_id": "pipeline_reject", "style": "danger",
                 "text": {"type": "plain_text", "text": "Reject"}, "value": value},
            ]},
        ]
        return self.send("chat.postMessage", {"channel": self.channel,
            "text": "Pipeline repair needs approval", "blocks": blocks})

    def verify(self, timestamp, signature, raw_body, now=None):
        """Verify Slack's v0 HMAC and reject replays older than five minutes."""
        if not self.enabled or not self.signing_secret or not timestamp or not signature:
            return False
        try:
            stamp = int(timestamp)
        except (TypeError, ValueError):
            return False
        current = int(time.time() if now is None else now)
        if abs(current - stamp) > 300:
            return False
        base = b"v0:" + str(stamp).encode() + b":" + raw_body
        expected = "v0=" + hmac.new(self.signing_secret.encode(), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


def slack_status():
    values = [configured(name) for name in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID",
              "SLACK_TEAM_ID", "SLACK_APP_ID", "SLACK_APPROVER_IDS")]
    return "configured, not verified" if all(values) else "Slack disabled: missing token, channel, workspace, app or approvers"
