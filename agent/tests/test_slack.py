import json
import time
from unittest.mock import Mock

import httpx
import pytest

from agent.server import create_app
from agent.slack import SlackNotifier


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    for key, value in {"SLACK_TEAM_ID": "T1", "SLACK_APP_ID": "A1", "SLACK_APPROVER_IDS": "U1"}.items():
        monkeypatch.setenv(key, value)
    post = Mock(return_value=httpx.Response(200, json={"ok": True, "channel": "C1", "ts": "123.4"}))
    notifier = SlackNotifier(token="test", channel="C1", post=post)
    decisions = []
    def runner(job_id, *, approval, incident_id, **kwargs):
        decision = approval(diagnosis="Numeric type changed", proposed_fix={"column": "amount"}, confidence=0.9)
        decisions.append(decision)
        return {"incident_id": incident_id, "outcome": "fixed" if decision["approved"] else "needs_human"}
    app = create_app(local_no_auth=True, state_dir=tmp_path, runner=runner, slack=notifier)
    client = app.test_client()
    client.post("/api/runs", json={"runId": "slack-test"})
    for _ in range(200):
        state = client.get("/api/runs/slack-test").json
        if state["slack"]:
            break
        time.sleep(0.005)
    assert state["approval"]
    payload = {"type": "block_actions", "team": {"id": "T1"}, "api_app_id": "A1",
               "channel": {"id": "C1"}, "user": {"id": "U1"}, "actions": [{
                   "action_id": "pipeline_approve", "value": json.dumps({
                       "incident_id": "slack-test", "fix_hash": state["approval"]["fix_hash"]})}]}
    yield app.extensions["slack_action"], payload, client, post, decisions
    client.post("/api/runs/slack-test/decision", json={"approved": False, "fix_hash": state["approval"]["fix_hash"]})


def test_slack_approval_identity_binding_and_result(bridge):
    action, payload, client, post, decisions = bridge
    for field in ("team", "channel", "user"):
        original = payload[field]["id"]
        payload[field]["id"] = "unauthorized"
        assert action(payload)[1] == 403
        payload[field]["id"] = original
    value = payload["actions"][0]["value"]
    payload["actions"][0]["value"] = json.dumps({"incident_id": "slack-test", "fix_hash": "stale"})
    assert action(payload)[1] == 409
    payload["actions"][0]["value"] = value
    assert action(payload)[1] == 200
    assert action(payload)[1] == 409
    for _ in range(200):
        if any(c.args[0].endswith("chat.update") for c in post.call_args_list):
            break
        time.sleep(0.005)
    assert decisions == [{"approved": True, "human_note": "Slack button decision", "subject": "slack:U1"}]
    assert post.call_args.args[0].endswith("chat.update")
    assert "pipeline_approve" not in json.dumps(post.call_args.kwargs["json"])


def test_browser_decision_wins_and_unsigned_http_rejected(bridge):
    action, payload, client, post, decisions = bridge
    assert client.post("/api/slack/actions", data={"payload": json.dumps(payload)}).status_code == 401
    ref = json.loads(payload["actions"][0]["value"])
    assert client.post("/api/runs/slack-test/decision", json={"approved": False, "fix_hash": ref["fix_hash"]}).status_code == 200
    assert action(payload)[1] == 409


def test_slack_failure_is_sanitized():
    notifier = SlackNotifier(token="secret", channel="C1", post=Mock(side_effect=httpx.ReadTimeout("secret")))
    assert notifier.send("chat.postMessage", {}) == {"status": "unavailable", "error": "Slack request failed"}


def test_rejection_and_expired_approval(bridge, monkeypatch):
    action, payload, client, post, decisions = bridge
    real_clock = time.monotonic
    monkeypatch.setattr("agent.server.monotonic", lambda: real_clock() + 1000)
    assert action(payload)[1] == 409
    monkeypatch.setattr("agent.server.monotonic", real_clock)
    payload["actions"][0]["action_id"] = "pipeline_reject"
    assert action(payload)[1] == 200
    for _ in range(200):
        if decisions:
            break
        time.sleep(0.005)
    assert decisions[0]["approved"] is False
