"""Request-level adapter checks. No external services are contacted by these tests."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt

from agent.auth import Auth0Verifier, Unauthorized, Forbidden
from agent.model_client import LiveModel
from agent.live import run_live
from agent.research_tools import search_repair_docs
from agent.reporting import export_report
from agent.server import create_app


class AdapterTests(unittest.TestCase):
    def test_authentication_failure_is_actionable_without_mutation(self):
        class AuthenticationError(Exception):
            pass

        events = []
        with patch.dict(os.environ, {"LLM_MODEL": "anthropic/test", "ANTHROPIC_API_KEY": "test-secret"}), \
                patch("pipeline.tools_pipeline.get_recent_logs", return_value={
                    "job_id": "job_1", "status": "failed", "error_type": "schema_drift",
                    "affected_table": "orders", "expected_row_count": 3,
                }), \
                patch("pipeline.tools_pipeline.get_schema", return_value={"table_name": "orders", "columns": []}), \
                patch("memory_approval.memory_store.search_past_incidents", return_value={"matches": []}), \
                patch("agent.live.search_repair_docs", return_value={"status": "unavailable", "sources": []}), \
                patch("pipeline.tools_pipeline.apply_fix") as apply:
            model = LiveModel(Mock(side_effect=AuthenticationError("test-secret")))
            result = run_live(model=model, approval=Mock(), report=False, notify=events.append)
        self.assertIn("AuthenticationError", result["reason"])
        self.assertIn("ANTHROPIC_API_KEY", result["reason"])
        self.assertNotIn("test-secret", json.dumps(result))
        self.assertEqual(result["mutation_state"], "not_attempted")
        self.assertEqual(result["model_calls"], 1)
        apply.assert_not_called()
        self.assertIn({"stage": "get_recent_logs", "status": "returned (pipeline: failed)"}, events)

    def test_live_model_uses_selected_model_and_no_hidden_retries(self):
        completion = Mock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":true}'))],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        ))
        with patch.dict(os.environ, {"LLM_MODEL": "anthropic/test", "ANTHROPIC_API_KEY": "test-secret"}):
            model = LiveModel(completion)
            self.assertEqual(model(system="system", prompt="prompt"), '{"answer":true}')
        self.assertEqual(completion.call_count, 1)
        self.assertEqual(completion.call_args.kwargs["num_retries"], 0)
        self.assertEqual(completion.call_args.kwargs["model"], "anthropic/test")
        self.assertNotIn("test-secret", json.dumps(model.calls))

    def test_provider_errors_do_not_expose_credentials(self):
        with patch.dict(os.environ, {"LLM_MODEL": "anthropic/test", "ANTHROPIC_API_KEY": "test-secret"}):
            model = LiveModel(Mock(side_effect=RuntimeError("test-secret")))
            with self.assertRaises(RuntimeError) as error:
                model(system="s", prompt="p")
        self.assertNotIn("test-secret", str(error.exception))
        self.assertEqual(len(model.calls), 1)

    def test_exa_request_contract_and_result_bounds(self):
        response = httpx.Response(200, json={"results": [
            {"title": "Docs", "url": "https://docs.example/test", "text": "x" * 3000}
        ] * 4}, request=httpx.Request("POST", "https://api.exa.ai/search"))
        with patch.dict(os.environ, {"EXA_API_KEY": "test-secret"}), patch("agent.research_tools.httpx.post", return_value=response) as post:
            result = search_repair_docs("public error")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["sources"]), 3)
        self.assertEqual(len(result["sources"][0]["excerpt"]), 2000)
        self.assertEqual(post.call_args.kwargs["json"]["numResults"], 3)
        self.assertFalse(post.call_args.kwargs["follow_redirects"])

    def test_exa_missing_key_never_contacts_network(self):
        with patch.dict(os.environ, {"EXA_API_KEY": ""}), patch("agent.research_tools.httpx.post") as post:
            result = search_repair_docs("test")
        self.assertEqual(result["status"], "unavailable")
        post.assert_not_called()

    def test_ambiguous_sends_document_once_and_redacts_configured_secrets(self):
        response = httpx.Response(201, json={"id": "doc_123"}, request=httpx.Request("POST", "https://app.ambiguous.ai/api/documents"))
        result = {"incident_id": "inc1", "outcome": "fixed", "reason": "secret-123456789"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"AMBIGUOUS_API_KEY": "secret-123456789"}), patch("agent.reporting.httpx.post", return_value=response) as post:
            first = export_report(result, state_dir=directory)
            second = export_report(result, state_dir=directory)
        self.assertEqual(first["status"], "sent")
        self.assertEqual(first, second)
        self.assertEqual(post.call_count, 1)
        self.assertNotIn("secret-123456789", json.dumps(post.call_args.kwargs["json"]))

    def test_ambiguous_timeout_blocks_automatic_replay(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"AMBIGUOUS_API_KEY": "test-secret"}), patch("agent.reporting.httpx.post", side_effect=httpx.ReadTimeout("secret")) as post:
            result = {"incident_id": "inc2", "outcome": "fixed", "reason": "verified"}
            self.assertEqual(export_report(result, state_dir=directory)["status"], "uncertain")
            self.assertEqual(export_report(result, state_dir=directory)["status"], "uncertain")
        self.assertEqual(post.call_count, 1)


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def verifier(self):
        with patch.dict(os.environ, {"AUTH0_DOMAIN": "test.auth0.com", "AUTH0_AUDIENCE": "pipeline"}):
            verifier = Auth0Verifier()
        verifier.keys = Mock()
        verifier.keys.get_signing_key_from_jwt.return_value = SimpleNamespace(key=self.private.public_key())
        return verifier

    def token(self, **overrides):
        payload = {"iss": "https://test.auth0.com/", "aud": "pipeline", "sub": "user-a",
                   "exp": int(time.time()) + 60, "permissions": ["approve:fixes"]}
        return "Bearer " + jwt.encode({**payload, **overrides}, self.private, algorithm="RS256")

    def test_valid_permission(self):
        self.assertEqual(self.verifier().verify(self.token(), "approve:fixes"), "user-a")

    def test_wrong_audience_expiry_or_signature_is_rejected(self):
        for overrides in ({"aud": "not-our-api"}, {"exp": 1}, {"iss": "https://other.auth0.com/"}):
            with self.subTest(overrides=overrides), self.assertRaises(Unauthorized):
                self.verifier().verify(self.token(**overrides), "approve:fixes")

    def test_viewer_cannot_approve(self):
        with self.assertRaises(Forbidden):
            self.verifier().verify(self.token(permissions=["read:incidents"]), "approve:fixes")


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.approvals = []
        def runner(job_id, *, approval, incident_id, inject_failure):
            decision = approval(diagnosis="test", proposed_fix={"fix_type": "schema_patch", "target": "orders", "change": {"column": "amount", "new_type": "float"}}, confidence=0.8)
            self.approvals.append(decision)
            return {"incident_id": incident_id, "job_id": job_id,
                    "outcome": "fixed" if decision["approved"] else "needs_human"}
        self.runner = runner
        class Verifier:
            def verify(self, token, permission):
                if token not in ("Bearer owner", "Bearer other", "Bearer viewer"):
                    raise Unauthorized("invalid token")
                if token == "Bearer viewer" and permission != "read:incidents":
                    raise Forbidden("viewer")
                return token
        self.app = create_app(runner=runner, verifier=Verifier(), state_dir=self.temp.name)
        self.client = self.app.test_client()
        self.headers = {"Authorization": "Bearer owner"}

    def wait_for(self, status):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = self.client.get("/api/runs/run1", headers=self.headers).get_json()
            if result.get("status") == status:
                return result
            time.sleep(0.005)
        self.fail("Worker did not reach " + status)

    def test_auth_required_and_approval_bound_to_hash_and_owner(self):
        self.assertEqual(self.client.post("/api/runs", json={"runId": "run1"}).status_code, 401)
        self.assertEqual(self.client.post("/api/runs", json={"runId": "run1"}, headers=self.headers).status_code, 202)
        run = self.wait_for("awaiting_approval")
        wrong = self.client.post("/api/runs/run1/decision", json={"approved": True, "fix_hash": "wrong"}, headers=self.headers)
        self.assertEqual(wrong.status_code, 409)
        forbidden = self.client.get("/api/runs/run1", headers={"Authorization": "Bearer other"})
        self.assertEqual(forbidden.status_code, 403)
        decision = {"approved": False, "fix_hash": run["approval"]["fix_hash"]}
        self.assertEqual(self.client.post("/api/runs/run1/decision", json=decision, headers={"Authorization": "Bearer viewer"}).status_code, 403)
        self.assertEqual(self.client.post("/api/runs/run1/decision", json=decision, headers=self.headers).status_code, 200)
        self.assertEqual(self.wait_for("finished")["result"]["outcome"], "needs_human")
        self.assertEqual(self.client.post("/api/runs/run1/decision", json=decision, headers=self.headers).status_code, 409)

    def test_ag_ui_events_and_approval_round_trip(self):
        response = self.client.post("/agent", json={"threadId": "thread1", "runId": "run1",
            "state": {}, "messages": [], "tools": [], "context": [], "forwardedProps": {}}, headers=self.headers, buffered=False)
        iterator = iter(response.response)
        events = [next(iterator).decode()]
        self.assertIn("RUN_STARTED", events[0])
        with self.app.test_client() as other_client:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = other_client.get("/api/runs/run1", headers=self.headers).get_json()
                if state.get("approval"):
                    break
                time.sleep(0.005)
            decision = {"approved": True, "fix_hash": state["approval"]["fix_hash"]}
            self.assertEqual(other_client.post("/api/runs/run1/decision", json=decision, headers=self.headers).status_code, 200)
        events.extend(chunk.decode() for chunk in iterator)
        response.close()
        self.assertIn("STATE_SNAPSHOT", "".join(events))
        self.assertIn("RUN_FINISHED", "".join(events))
        self.assertEqual(len(self.approvals), 1)

    def test_persisted_run_cannot_restart_after_server_restart(self):
        self.client.post("/api/runs", json={"runId": "run1"}, headers=self.headers)
        pending = self.wait_for("awaiting_approval")
        self.client.post("/api/runs/run1/decision", json={"approved": False, "fix_hash": pending["approval"]["fix_hash"]}, headers=self.headers)
        self.wait_for("finished")
        # Use loopback mode to test replay protection independently of the injected verifier.
        restarted = create_app(local_no_auth=True, runner=Mock(), state_dir=self.temp.name)
        self.assertEqual(restarted.test_client().post("/api/runs", json={"runId": "run1"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
