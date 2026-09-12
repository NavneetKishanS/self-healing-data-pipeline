import copy
import inspect
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from memory_approval import approvals, auth, memory_store, report_export
from memory_approval.approval_server import app, request_approval
from memory_approval.storage import runtime_path

FIX = {"fix_type": "schema_patch", "target": "orders",
       "change": {"column": "amount", "new_type": "float"}}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_C_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APPROVAL_AUTH_MODE", "local")
    app.config.update(TESTING=True)


def payload(record, decision="approve"):
    return {**{key: record[key] for key in ("approval_id", "incident_id", "fix_hash", "decision_token")},
            "decision": decision, "note": "Reviewed."}


def incident(identifier="test-1"):
    return {"incident_id": identifier, "error_type": "schema_drift", "root_cause": "Type changed",
            "fix_applied": copy.deepcopy(FIX), "outcome": "resolved"}


def pending_record():
    for _ in range(200):
        records = approvals.list_pending()
        if records:
            return records[0]
        time.sleep(0.01)
    pytest.fail("Approval was not created")


@pytest.mark.parametrize("decision,expected", [("approve", True), ("reject", False)])
def test_blocking_round_trip(decision, expected):
    os_timeout = "3"
    with ThreadPoolExecutor() as pool:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("APPROVAL_TIMEOUT_SECONDS", os_timeout)
            waiter = pool.submit(request_approval, "Changed schema", FIX, 0.8)
            record = pending_record()
            with app.test_client() as client:
                response = client.post("/api/decide", json=payload(record, decision))
                assert response.status_code == 200
            assert waiter.result(timeout=3) == {"approved": expected, "human_note": "Reviewed."}
        assert approvals.get_approval(record["approval_id"])["decided_by"] == "local-operator"


def test_timeout_is_rejected_and_late_click_fails():
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("APPROVAL_TIMEOUT_SECONDS", "0.02")
        assert request_approval("Changed schema", FIX, 0.8)["approved"] is False
    record = next(iter(json.loads(runtime_path("approvals.json").read_text()).values()))
    assert record["status"] == "expired"
    assert not approvals.decide_approval(payload(record))


def test_exact_payload_claimed_once():
    with approvals.approval_context("run-1"):
        record = approvals.create_approval("Changed schema", FIX, 0.8, 60)
    assert approvals.decide_approval(payload(record))
    changed = {**FIX, "target": "other-table"}
    assert not approvals.consume_approval("run-1", changed)
    assert not approvals.consume_approval("other-run", FIX)
    assert approvals.consume_approval("run-1", FIX)
    assert not approvals.consume_approval("run-1", FIX)


def test_conflicting_clicks_only_one_wins():
    record = approvals.create_approval("Changed schema", FIX, 0.8, 60)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(approvals.decide_approval, payload(record, d)) for d in ("approve", "reject")]
        assert sorted(f.result() for f in futures) == [False, True]


@pytest.mark.parametrize("field", ["incident_id", "fix_hash", "decision_token"])
def test_forged_or_stale_decision(field):
    record = approvals.create_approval("Changed schema", FIX, 0.8, 60)
    data = payload(record)
    data[field] = "wrong"
    assert not approvals.decide_approval(data)
    assert approvals.get_approval(record["approval_id"])["status"] == "pending"


def test_mutating_input_while_waiting_does_not_authorize_changed_fix():
    fix = copy.deepcopy(FIX)
    with ThreadPoolExecutor() as pool:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("APPROVAL_TIMEOUT_SECONDS", "3")
            waiter = pool.submit(request_approval, "Changed schema", fix, 0.8)
            record = pending_record()
            fix["change"]["new_type"] = "integer"
            assert approvals.decide_approval(payload(record))
            assert waiter.result(timeout=3)["approved"] is False
    assert approvals.get_approval(record["approval_id"])["proposed_fix"] == FIX


def test_public_tool_signatures_match_context():
    assert list(inspect.signature(request_approval).parameters) == [
        "diagnosis", "proposed_fix", "confidence"
    ]
    assert list(inspect.signature(memory_store.search_past_incidents).parameters) == ["error_type"]
    assert list(inspect.signature(memory_store.log_incident).parameters) == ["incident"]


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -1, 1.1, True, "0.8"])
def test_invalid_confidence(confidence):
    with pytest.raises(ValueError):
        approvals.create_approval("Changed schema", FIX, confidence, 60)


def test_flask_form_escapes_evidence_and_round_trips():
    record = approvals.create_approval('<script>alert("x")</script>', FIX, 0.8, 60)
    with app.test_client() as client:
        html = client.get("/").text
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html
        assert record["decision_token"] in html
        assert client.post("/decide", data=payload(record)).status_code == 200
        assert client.post("/decide", data=payload(record)).status_code == 409
        assert "No pending approvals" in client.get("/").text


def test_ag_ui_snapshot_and_decision_use_persisted_records():
    record = approvals.create_approval("Changed schema", FIX, 0.8, 60)
    with app.test_client() as client:
        body = {"threadId": "thread-1", "runId": "run-1", "forwardedProps": {}}
        response = client.post("/ag-ui", json=body)
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert [e["type"] for e in events] == ["RUN_STARTED", "STATE_SNAPSHOT", "RUN_FINISHED"]
        assert events[1]["snapshot"]["approvals"][0]["fix_hash"] == record["fix_hash"]
        body["forwardedProps"] = {"decision": payload(record, "reject")}
        assert client.post("/ag-ui", json=body).status_code == 200
        assert approvals.get_approval(record["approval_id"])["status"] == "rejected"


def test_memory_deduplicates_and_preserves_seed():
    original = memory_store.SEED_PATH.read_bytes()
    assert len(memory_store.search_past_incidents("schema_drift")["matches"]) == 2
    assert memory_store.log_incident(incident()) == {"logged": True}
    assert memory_store.log_incident(incident()) == {"logged": True}
    assert len(memory_store.search_past_incidents("schema_drift")["matches"]) == 3
    assert memory_store.log_incident({**incident(), "root_cause": "conflict"}) == {"logged": False}
    assert memory_store.SEED_PATH.read_bytes() == original


def test_corrupt_memory_not_overwritten():
    path = runtime_path("incidents.json")
    path.write_text("not JSON")
    assert memory_store.log_incident(incident()) == {"logged": False}
    assert path.read_text() == "not JSON"
    with pytest.raises(ValueError):
        memory_store.search_past_incidents("schema_drift")


def test_concurrent_memory_writes_preserved():
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda i: memory_store.log_incident(incident(f"run-{i}")), range(20)))
    assert all(r["logged"] for r in results)
    assert len(json.loads(runtime_path("incidents.json").read_text())) == 20


def test_rejected_incident_cannot_be_logged_as_historical_outcome():
    assert memory_store.log_incident({**incident(), "outcome": "needs_human"}) == {"logged": False}


def _auth_token(monkeypatch, **overrides):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {
        "sub": "auth0|reviewer-1", "iss": "https://demo.eu.auth0.com/",
        "aud": "https://pipeline-demo-api", "exp": int(time.time()) + 60,
        "permissions": ["read:incidents", "approve:fixes"],
    }
    claims.update(overrides)
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    client = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private_key.public_key()))
    monkeypatch.setattr(auth, "_jwks_client", lambda _: client)
    monkeypatch.setenv("AUTH0_DOMAIN", "demo.eu.auth0.com")
    monkeypatch.setenv("AUTH0_AUDIENCE", "https://pipeline-demo-api")
    return token


def test_auth0_validates_signature_claims_and_permissions(monkeypatch):
    token = _auth_token(monkeypatch)
    assert auth.verify_request(f"Bearer {token}", "approve:fixes") == (
        "auth0|reviewer-1", None, 200
    )
    assert auth.verify_request(f"Bearer {token}", "admin:anything")[2] == 403
    assert auth.verify_request("", "read:incidents")[2] == 401


def test_auth0_rejects_expired_and_wrong_audience_tokens(monkeypatch):
    expired = _auth_token(monkeypatch, exp=int(time.time()) - 1)
    assert auth.verify_request(f"Bearer {expired}", "read:incidents")[2] == 401
    wrong_audience = _auth_token(monkeypatch, aud="https://other-api")
    assert auth.verify_request(f"Bearer {wrong_audience}", "read:incidents")[2] == 401


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _export_incident(identifier="export-1"):
    return {
        "incident_id": identifier,
        "diagnosis": "Column type changed upstream.",
        "approval_decision": "approved",
        "observed_outcome": "fixed",
        "sources": [{"title": "Pandas conversion", "url": "https://example.com/docs",
                     "excerpt": "Use explicit numeric conversion."}],
        "raw_logs": "SECRET DATABASE PASSWORD MUST NOT BE EXPORTED",
    }


def test_ambiguous_export_is_redacted_and_idempotent(monkeypatch):
    monkeypatch.setenv("AMBIGUOUS_API_KEY", "secret-api-key")
    requests = []

    def fake_open(request, timeout):
        requests.append((request, timeout))
        return _FakeResponse(b'{"id":"doc_123"}')

    monkeypatch.setattr(report_export, "urlopen", fake_open)
    expected = {"exported": True, "document_id": "doc_123",
                "document_url": "https://app.ambiguous.ai/docs/doc_123", "error": None}
    assert report_export.report_export(_export_incident()) == expected
    assert report_export.report_export(_export_incident()) == expected
    assert len(requests) == 1
    body = requests[0][0].data.decode()
    assert "SECRET DATABASE PASSWORD" not in body
    assert "secret-api-key" not in body
    assert requests[0][0].get_header("Authorization") == "Bearer secret-api-key"


def test_ambiguous_failure_is_nonfatal_and_not_retried(monkeypatch):
    monkeypatch.setenv("AMBIGUOUS_API_KEY", "secret-api-key")
    calls = 0

    def fail_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise OSError("network details should not leak")

    monkeypatch.setattr(report_export, "urlopen", fail_once)
    first = report_export.report_export(_export_incident("export-failure"))
    second = report_export.report_export(_export_incident("export-failure"))
    assert calls == 1
    assert first["exported"] is second["exported"] is False
    assert "network details" not in first["error"]


def test_ambiguous_missing_key_does_not_create_attempt():
    assert report_export.report_export(_export_incident())["error"] == (
        "AMBIGUOUS_API_KEY is not configured"
    )
    assert not runtime_path("report_exports.json").exists()
