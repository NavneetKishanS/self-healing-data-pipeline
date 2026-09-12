"""Offline workflow tests: scripted reasoning, stateful tools, no vendor calls."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from jinja2 import UndefinedError

from agent import run_incident
from agent.prompts import render
from agent.validation import fingerprint, parse_response
from memory_approval.procedural_graph import ProceduralGraph

FIX = {"fix_type": "schema_patch", "target": "orders", "change": {"column": "amount", "new_type": "float"}}
RETRY_FIX = {"fix_type": "retry_policy", "target": "orders", "change": {"max_retries": 5}}
INDEX_FIX = {"fix_type": "config_change", "target": "orders", "change": {"action": "add_index"}}
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
        self.procedures = None
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
                            model=self.model, incident_id="inc_test", procedures=self.procedures)


class FakeProcedures:
    """Scripted procedural graph: records every call, returns what the test configures."""

    def __init__(self, pruned=(), fast_path=None, fail_localize=False, fail_refine=False, garbage=False):
        self.pruned, self.fast_path = list(pruned), fast_path
        self.fail_localize, self.fail_refine, self.garbage = fail_localize, fail_refine, garbage
        self.localized, self.refined = [], []

    def localize(self, stage, error_type, proposal=None):
        self.localized.append((stage, error_type, deepcopy(proposal)))
        if self.fail_localize:
            raise RuntimeError("graph file secret path")
        if self.garbage:
            return {"procedures": "not an object"}
        return {"revision": 4, "fast_path": self.fast_path, "pruned_fix_types": self.pruned,
                "procedures": {"error_type": error_type, "marker": "PROCEDURE_" + stage.upper()}}

    def refine(self, result):
        self.refined.append(deepcopy(result))
        if self.fail_refine:
            raise RuntimeError("disk secret")
        return {"event": result["terminal_event"], "changed": True, "committed": True, "revision": 5,
                "changes": ["reinforced"], "reason": None}


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
        self.assertEqual((result["error_type"], result["terminal_event"], result["procedures"]),
                         ("schema_drift", "verified", None))
        self.assertEqual(result["application"], {"applied": True, "message": "patched"})
        for prompt in f.prompts:
            self.assertNotIn("Procedural memory", prompt["prompt"])
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
        f = Fixture()
        with self.assertRaises(ValueError):
            run_incident("job_1", tools=f.tools, model=f.model, procedures=object())
        self.assertEqual(f.calls, [])

    def test_terminal_events_are_structured(self):
        cases = {
            "rejected": lambda f: f.tools.update(request_approval=lambda **kwargs: {"approved": False, "human_note": "no"}),
            "critique_disagreed": lambda f: f.answers.__setitem__(1, json.dumps({**CRITIQUE, "agrees": False})),
            "apply_failed": lambda f: f.tools.update(apply_fix=lambda fix: {"applied": False, "message": "refused"}),
            "verification_failed": lambda f: f.tools.update(rerun_pipeline=lambda job_id: {"job_id": job_id, "status": "failed", "row_count": 0}),
            "job_not_failed": lambda f: f.logs.update(status="success"),
            "model_invalid": lambda f: f.answers.__setitem__(slice(None), ["bad", "bad"]),
        }
        for event, arrange in cases.items():
            with self.subTest(event=event):
                f = Fixture()
                arrange(f)
                result = f.run()
                self.assertEqual(result["terminal_event"], event)
                self.assertNotEqual(result["outcome"], "fixed")
        f = Fixture()
        def tamper(**kwargs):
            kwargs["proposed_fix"]["change"]["new_type"] = "string"
            return {"approved": True}
        f.tools["request_approval"] = tamper
        self.assertEqual(f.run()["terminal_event"], "approval_tampered")

    def test_procedures_shape_both_prompts_and_learn_after_the_outcome(self):
        f = Fixture()
        f.procedures = FakeProcedures(fast_path={"fix_type": "schema_patch", "successes": 3})
        result = f.run()
        self.assertEqual(result["outcome"], "fixed")
        # Guidance is not a tool call and refinement is not a model call.
        self.assertEqual((result["tool_calls"], result["model_calls"]), (7, 2))
        self.assertEqual(f.procedures.localized, [("diagnose", "schema_drift", None), ("critique", "schema_drift", FIX)])
        self.assertIn("PROCEDURE_DIAGNOSE", f.prompts[0]["prompt"])
        self.assertIn("PROCEDURE_CRITIQUE", f.prompts[1]["prompt"])
        self.assertNotIn("PROCEDURE_CRITIQUE", f.prompts[0]["prompt"])
        self.assertEqual(result["procedures"], {
            "revision": 4, "fast_path": {"fix_type": "schema_patch", "successes": 3}, "guard": None,
            "refinement": {"event": "verified", "changed": True, "committed": True, "revision": 5,
                           "changes": ["reinforced"], "reason": None}})
        # The refiner sees the final record: outcome, event, approval note, and the applied fix.
        self.assertEqual(len(f.procedures.refined), 1)
        seen = f.procedures.refined[0]
        self.assertEqual((seen["outcome"], seen["terminal_event"], seen["approval"]["human_note"]), ("fixed", "verified", "reviewed"))
        self.assertEqual(seen["diagnosis"]["proposed_fix"], FIX)
        self.assertEqual([t["name"] for t in result["trace"] if t["kind"] == "procedure"], ["diagnose", "critique", "refine"])
        self.assertEqual(result["trace"][-1], {**result["trace"][-1], "kind": "procedure", "name": "refine", "status": "committed"})

    def test_pruned_proposal_is_blocked_before_critique_and_approval(self):
        f = Fixture()
        f.procedures = FakeProcedures(pruned=["schema_patch"])
        result = f.run()
        self.assertEqual((result["outcome"], result["terminal_event"]), ("needs_human", "proposal_pruned"))
        self.assertEqual((result["model_calls"], result["mutation_state"]), (1, "not_attempted"))
        self.assertNotIn("request_approval", f.calls)
        self.assertEqual(result["procedures"]["guard"], "blocked")
        self.assertEqual(f.column_type, "string")
        self.assertEqual(f.procedures.refined[0]["terminal_event"], "proposal_pruned")

    def test_procedure_failures_are_warnings_not_outcomes(self):
        for kwargs in ({"fail_localize": True}, {"garbage": True}, {"fail_refine": True}):
            with self.subTest(kwargs=kwargs):
                f = Fixture()
                f.procedures = FakeProcedures(**kwargs)
                result = f.run()
                self.assertEqual((result["outcome"], result["tool_calls"], result["model_calls"]), ("fixed", 7, 2))
                self.assertTrue(result["warnings"])
                self.assertNotIn("secret", json.dumps(result))
                for prompt in f.prompts:
                    self.assertEqual("Procedural memory" in prompt["prompt"], "fail_refine" in kwargs)
                json.dumps(result, allow_nan=False)

    def test_graph_learns_from_rejection_and_blocks_the_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = ProceduralGraph(path=Path(directory) / "graph.json", smoke_test=lambda: (True, "ok"))

            def timeout_fixture(fix, note="reviewed", approved=True):
                f = Fixture()
                f.logs.update(error_type="timeout", error_message="Job exceeded 300s runtime limit")
                f.answers[0] = json.dumps({**DIAGNOSIS, "proposed_fix": fix})
                f.tools["request_approval"] = lambda **kwargs: {"approved": approved, "human_note": note}
                f.tools["apply_fix"] = lambda fix: {"applied": True, "message": "applied"}
                f.tools["rerun_pipeline"] = lambda job_id: {"job_id": job_id, "status": "success", "row_count": 1000}
                f.procedures = graph
                return f

            # Incident 1: a retry is proposed; the on-call engineer rejects it with a reason.
            first = timeout_fixture(RETRY_FIX, note="Never raise retries for timeouts; add the index", approved=False)
            result = first.run()
            self.assertEqual((result["outcome"], result["terminal_event"]), ("needs_human", "rejected"))
            self.assertTrue(result["procedures"]["refinement"]["committed"])
            self.assertIn("pruned diagnose -> retry_policy for timeout", result["procedures"]["refinement"]["changes"])

            # Incident 2: the rejection note is in the diagnose prompt; the validated repair succeeds.
            second = timeout_fixture(INDEX_FIX)
            result = second.run()
            self.assertEqual(result["outcome"], "fixed")
            self.assertIn("Never raise retries for timeouts; add the index", second.prompts[0]["prompt"])
            self.assertIn('"status": "pruned"', second.prompts[0]["prompt"])
            self.assertIn('"proposal": {"fix_type": "config_change", "status": "admissible"}', second.prompts[1]["prompt"])
            self.assertEqual(result["procedures"]["fast_path"]["fix_type"], "config_change")
            self.assertEqual(result["procedures"]["refinement"]["revision"], 2)

            # Incident 3: the model ignores the guidance; Python blocks the pruned repair without approval.
            third = timeout_fixture(RETRY_FIX)
            result = third.run()
            self.assertEqual((result["outcome"], result["terminal_event"], result["model_calls"]), ("needs_human", "proposal_pruned", 1))
            self.assertNotIn("request_approval", third.calls)
            self.assertFalse(result["procedures"]["refinement"]["changed"])
            self.assertEqual(graph.load()["revision"], 2)


if __name__ == "__main__":
    unittest.main()
