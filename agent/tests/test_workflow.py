"""Offline workflow tests: scripted reasoning, stateful tools, no vendor calls."""

from copy import deepcopy
import json
import unittest

from jinja2 import UndefinedError

from agent import run_incident
from agent.prompts import render
from agent.validation import fingerprint, parse_response

FIX = {"fix_type": "schema_patch", "target": "orders", "change": {"column": "amount", "new_type": "float"}}
DIAGNOSIS = {"diagnosis": "Amount changed type; logs identify the required float conversion.",
             "proposed_fix": FIX, "confidence": 0.8}
CRITIQUE = {"agrees": True, "counter_argument": "Bad string values could still fail conversion.",
            "revised_confidence": 0.7}


class Fixture:
    def __init__(self):
        self.calls = []
        self.prompts = []
        self.answers = [json.dumps(DIAGNOSIS), json.dumps(CRITIQUE)]
        self.column_type = "string"
        self.incidents = []
        self.logs = {"job_id": "job_1", "status": "failed", "error_type": "schema_drift",
                     "error_message": "Cannot cast amount string to float", "affected_table": "orders",
                     "expected_row_count": 1000, "row_count": 1000, "timestamp": "2026-09-12T00:00:00Z"}
        self.tools = {
            "get_recent_logs": lambda job_id: deepcopy(self.logs),
            "get_schema": lambda table_name: {"table_name": table_name, "columns": [
                {"name": "amount", "type": self.column_type, "nullable": False}], "last_changed": None},
            "search_past_incidents": lambda error_type: {"matches": []},
            "request_approval": lambda **kwargs: {"approved": True, "human_note": "reviewed"},
            "apply_fix": self.apply,
            "rerun_pipeline": self.rerun,
            "log_incident": self.log,
        }

    def apply(self, fix):
        self.column_type = fix["change"]["new_type"]
        return {"applied": True, "message": "patched"}

    def rerun(self, job_id):
        return {"job_id": job_id, "status": "success" if self.column_type == "float" else "failed",
                "row_count": 1000}

    def log(self, incident):
        self.incidents.append(deepcopy(incident))
        return {"logged": True}

    def model(self, **kwargs):
        self.prompts.append(kwargs)
        return self.answers.pop(0)

    def run(self):
        def wrap(name, fn):
            def call(**kwargs):
                self.calls.append(name)
                return fn(**kwargs)
            return call
        return run_incident("job_1", tools={name: wrap(name, fn) for name, fn in self.tools.items()},
                            model=self.model, incident_id="inc_test")


class WorkflowTests(unittest.TestCase):
    def test_success_uses_actual_fixture_state_and_exact_order(self):
        f = Fixture()
        result = f.run()
        self.assertEqual(f.calls, ["get_recent_logs", "get_schema", "search_past_incidents",
                                   "request_approval", "apply_fix", "rerun_pipeline", "log_incident"])
        self.assertEqual((result["outcome"], result["tool_calls"], result["model_calls"]), ("fixed", 7, 2))
        self.assertEqual(f.column_type, "float")
        self.assertEqual(f.incidents[0]["fix_applied"], FIX)
        self.assertEqual(result["approval"]["fix_hash"], fingerprint(FIX))
        self.assertEqual(result["approval"]["incident_id"], "inc_test")
        json.dumps(result, allow_nan=False)

    def test_rejection_and_non_boolean_approval_never_write(self):
        for approved in (False, "true", 1, None):
            with self.subTest(approved=approved):
                f = Fixture()
                f.tools["request_approval"] = lambda **kwargs: {"approved": approved}
                result = f.run()
                self.assertEqual(result["outcome"], "needs_human")
                self.assertNotIn("apply_fix", f.calls)
                self.assertEqual(f.column_type, "string")

    def test_disagreement_stops_before_approval(self):
        f = Fixture()
        f.answers[1] = json.dumps({**CRITIQUE, "agrees": False})
        self.assertEqual(f.run()["outcome"], "needs_human")
        self.assertNotIn("request_approval", f.calls)

    def test_one_correction_can_recover_either_stage(self):
        for index in (0, 1):
            with self.subTest(index=index):
                f = Fixture()
                f.answers.insert(index, "not JSON")
                result = f.run()
                self.assertEqual((result["outcome"], result["model_calls"]), ("fixed", 3))

    def test_retry_allowance_is_shared_across_stages(self):
        f = Fixture()
        f.answers = ["bad", json.dumps(DIAGNOSIS), "bad", json.dumps(CRITIQUE)]
        result = f.run()
        self.assertEqual((result["outcome"], result["model_calls"]), ("gave_up", 3))
        self.assertNotIn("request_approval", f.calls)

    def test_persistent_invalid_diagnosis_stops_after_two_calls(self):
        f = Fixture()
        f.answers = ["bad", "bad"]
        result = f.run()
        self.assertEqual((result["outcome"], result["model_calls"]), ("gave_up", 2))
        self.assertNotIn("apply_fix", f.calls)

    def test_changed_approval_payload_is_rejected(self):
        f = Fixture()
        def approve(**kwargs):
            kwargs["proposed_fix"]["change"]["new_type"] = "string"
            return {"approved": True}
        f.tools["request_approval"] = approve
        result = f.run()
        self.assertEqual(result["outcome"], "needs_human")
        self.assertFalse(result["approval"]["approved"])
        self.assertNotIn("apply_fix", f.calls)
        self.assertEqual(result["diagnosis"]["proposed_fix"], FIX)

    def test_failed_and_uncertain_mutations_are_not_retried(self):
        for failure in (False, "timeout"):
            with self.subTest(failure=failure):
                f = Fixture()
                def apply(**kwargs):
                    if failure == "timeout":
                        raise TimeoutError("secret provider message")
                    return {"applied": False}
                f.tools["apply_fix"] = apply
                result = f.run()
                self.assertEqual(result["outcome"], "needs_human")
                self.assertEqual(f.calls.count("apply_fix"), 1)
                self.assertNotIn("rerun_pipeline", f.calls)
                self.assertNotIn("secret provider message", json.dumps(result))

    def test_failed_or_wrong_rerun_never_records_resolution(self):
        for response in (
            {"job_id": "job_1", "status": "failed", "row_count": 1000},
            {"job_id": "job_1", "status": "success", "row_count": 999},
            {"job_id": "other", "status": "success", "row_count": 1000},
        ):
            with self.subTest(response=response):
                f = Fixture()
                f.tools["rerun_pipeline"] = lambda **kwargs: response
                self.assertEqual(f.run()["outcome"], "needs_human")
                self.assertNotIn("log_incident", f.calls)

    def test_claiming_applied_without_changing_state_does_not_pass(self):
        f = Fixture()
        f.tools["apply_fix"] = lambda **kwargs: {"applied": True}
        self.assertEqual(f.run()["outcome"], "needs_human")
        self.assertEqual(f.incidents, [])



    def test_memory_failure_does_not_replay_verified_repair(self):
        f = Fixture()
        f.tools["log_incident"] = lambda **kwargs: {"logged": False}
        result = f.run()
        self.assertEqual(result["outcome"], "fixed")
        self.assertFalse(result["memory_logged"])
        self.assertTrue(result["warnings"])
        self.assertEqual(f.calls.count("apply_fix"), 1)

    def test_unknown_or_healthy_jobs_do_not_repair(self):
        for status in ("unknown", "success"):
            f = Fixture()
            f.logs["status"] = status
            result = f.run()
            self.assertEqual(result["outcome"], "needs_human")
            self.assertEqual(f.prompts, [])
            self.assertEqual(f.calls, ["get_recent_logs"])

    def test_model_exception_is_not_an_output_correction(self):
        f = Fixture()
        def model(**kwargs):
            raise RuntimeError("API key is secret")
        f.model = model
        result = f.run()
        self.assertEqual((result["outcome"], result["model_calls"]), ("gave_up", 1))
        self.assertNotIn("API key", json.dumps(result))

    def test_invalid_confidence_and_boolean_types(self):
        for confidence in (True, -0.1, 1.1, float("nan"), float("inf"), "0.8"):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                parse_response("diagnose", json.dumps({**DIAGNOSIS, "confidence": confidence}))
        with self.assertRaises(ValueError):
            parse_response("critique", json.dumps({**CRITIQUE, "agrees": "false"}))

    def test_templates_fail_on_missing_evidence_and_do_not_execute_input(self):
        with self.assertRaises(UndefinedError):
            render("diagnose")
        text = render("diagnose", evidence={"log": "{{ 7 * 7 }}"})
        self.assertIn("{{ 7 * 7 }}", text)
        with self.assertRaises(ValueError):
            render("../CONTEXT.md")

    def test_configuration_failure_happens_before_tools(self):
        with self.assertRaises(ValueError):
            run_incident("job_1", tools={}, model=lambda **kwargs: "{}")


if __name__ == "__main__":
    unittest.main()
