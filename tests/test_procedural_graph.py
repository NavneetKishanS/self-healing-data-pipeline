import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from memory_approval import procedural_graph
from memory_approval.procedural_graph import ProceduralGraph, validate_graph
from memory_approval.storage import runtime_path

SCHEMA_FIX = {"fix_type": "schema_patch", "target": "orders", "change": {"column": "amount", "new_type": "float"}}
RETRY_FIX = {"fix_type": "retry_policy", "target": "orders", "change": {"max_retries": 5}}
INDEX_FIX = {"fix_type": "config_change", "target": "orders", "change": {"action": "add_index"}}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_C_DATA_DIR", str(tmp_path))


def graph(smoke=lambda: (True, "ok")):
    return ProceduralGraph(smoke_test=smoke)


def run_record(event, fix, error_type="schema_drift", incident_id="run-1", **extra):
    record = {"incident_id": incident_id, "error_type": error_type, "terminal_event": event,
              "diagnosis": {"diagnosis": "Amount arrived as text.", "proposed_fix": copy.deepcopy(fix), "confidence": 0.8},
              "approval": None, "verification": None, "application": None}
    record.update(extra)
    return record


def fix_edge(data, fix_type, error_type):
    return next(e for e in data["edges"] if e["source"] == "diagnose" and e["target"] == fix_type
                and error_type in e.get("condition", {}).get("error_type", [error_type]))


def test_seed_is_valid_and_materializes_once_without_touching_the_seed_file():
    original = procedural_graph.SEED_PATH.read_bytes()
    validate_graph(json.loads(original))
    path = runtime_path("procedural_graph.json")
    assert not path.exists()
    first = graph().load()
    assert path.exists() and first["revision"] == 0
    assert json.loads(path.read_text()) == first
    assert procedural_graph.SEED_PATH.read_bytes() == original


def test_diagnose_view_localizes_two_hops_and_marks_fast_path():
    view = graph().localize("diagnose", "schema_drift")
    steps = [t["step"] for t in view["procedures"]["transitions"]]
    assert steps == ["schema -> diagnose", "diagnose -> schema_patch", "diagnose -> retry_policy", "schema_patch -> rerun"]
    statuses = {t["step"]: t.get("status") for t in view["procedures"]["transitions"]}
    assert statuses["diagnose -> schema_patch"] == "admissible"
    assert statuses["diagnose -> retry_policy"] == "pruned"
    assert view["fast_path"] == {"fix_type": "schema_patch", "successes": 1,
                                 "validated_fix": {"target": "orders", "change": SCHEMA_FIX["change"]}}
    assert view["pruned_fix_types"] == ["retry_policy"]
    assert view["revision"] == 0
    # Only condition-matching edges appear: nothing about null_spike or timeout leaks in.
    assert "config_change" not in json.dumps(view)
    json.dumps(view, allow_nan=False)


def test_alias_and_unknown_error_types():
    assert graph().localize("diagnose", "schema_mismatch")["pruned_fix_types"] == ["retry_policy"]
    view = graph().localize("diagnose", "unheard_of")
    assert view["procedures"]["transitions"] == [] and view["fast_path"] is None and view["pruned_fix_types"] == []
    assert graph().localize("diagnose", "timeout")["fast_path"]["fix_type"] == "config_change"


def test_critique_view_centres_on_the_proposal():
    pruned = graph().localize("critique", "schema_drift", RETRY_FIX)["procedures"]
    assert pruned["proposal"] == {"fix_type": "retry_policy", "status": "pruned"}
    assert [t["step"] for t in pruned["transitions"]] == ["diagnose -> retry_policy"]
    fine = graph().localize("critique", "schema_drift", SCHEMA_FIX)["procedures"]
    assert fine["proposal"]["status"] == "admissible"
    assert [t["step"] for t in fine["transitions"]] == ["diagnose -> schema_patch", "schema_patch -> rerun"]
    unknown = graph().localize("critique", "schema_drift", {"fix_type": "config_change"})["procedures"]
    assert unknown["proposal"]["status"] == "unknown"
    # No fix edge exists for this pairing, but the unconditioned verification step still applies.
    assert [t["step"] for t in unknown["transitions"]] == ["config_change -> rerun"]
    assert graph().localize("critique", "schema_drift", None)["procedures"]["proposal"]["status"] == "unknown"


@pytest.mark.parametrize("stage,error_type", [("plan", "schema_drift"), ("diagnose", ""), ("diagnose", None)])
def test_localize_rejects_bad_arguments(stage, error_type):
    with pytest.raises(ValueError):
        graph().localize(stage, error_type)


def test_verified_success_reinforces_edge_and_remembers_the_fix():
    g = graph()
    summary = g.refine(run_record("verified", SCHEMA_FIX, incident_id="run-9"))
    assert summary["committed"] and summary["changed"] and summary["revision"] == 1
    assert summary["changes"] == ["reinforced diagnose -> schema_patch for schema_drift (2 validated)"]
    edge = fix_edge(g.load(), "schema_patch", "schema_drift")
    assert edge["evidence"]["successes"] == 2 and edge["evidence"]["incidents"][-1] == "run-9"
    # Same fix again is deduplicated, not appended twice.
    assert edge["validated_fixes"] == [{"incident_id": "run-9", "fix": SCHEMA_FIX}]
    assert g.load()["changelog"][-1]["incident_id"] == "run-9"
    assert g.localize("diagnose", "schema_drift")["fast_path"]["successes"] == 2


def test_success_for_a_new_error_type_creates_admissible_edge_and_verification_step():
    g = graph()
    summary = g.refine(run_record("verified", INDEX_FIX, error_type="slow_join", incident_id="run-2"))
    assert summary["committed"]
    data = g.load()
    edge = fix_edge(data, "config_change", "slow_join")
    assert edge["relation"] == "ADMISSIBLE" and edge["condition"] == {"error_type": ["slow_join"]}
    assert edge["guidance"].startswith("Validated on run-2: config_change on orders")
    assert any(e["source"] == "config_change" and e["target"] == "rerun" for e in data["edges"])
    view = g.localize("diagnose", "slow_join")
    assert view["fast_path"]["fix_type"] == "config_change"
    assert [t["step"] for t in view["procedures"]["transitions"]] == ["diagnose -> config_change", "config_change -> rerun"]


def test_human_rejection_prunes_untested_repair_and_keeps_the_note():
    g = graph()
    note = "Don't bump retries,\x00 this is an upstream schema bug\n\n  Bearer abc.def.ghi"
    summary = g.refine(run_record("rejected", RETRY_FIX, error_type="timeout", incident_id="run-3",
                                  approval={"approved": False, "human_note": note}))
    assert summary["committed"]
    edge = fix_edge(g.load(), "retry_policy", "timeout")
    assert edge["relation"] == "PRUNED" and edge["evidence"]["rejections"] == 1
    assert edge["pitfalls"] == ["run-3: rejected by human review — Don't bump retries, this is an upstream schema bug [REDACTED]"]
    assert "pruned diagnose -> retry_policy for timeout" in summary["changes"]
    assert g.localize("diagnose", "timeout")["pruned_fix_types"] == ["retry_policy"]
    assert g.localize("critique", "timeout", RETRY_FIX)["procedures"]["proposal"]["status"] == "pruned"


def test_rejection_of_a_validated_repair_adds_pitfall_but_does_not_prune():
    g = graph()
    g.refine(run_record("verified", SCHEMA_FIX, incident_id="run-a"))
    summary = g.refine(run_record("rejected", SCHEMA_FIX, incident_id="run-b",
                                  approval={"approved": False, "human_note": "Not this time"}))
    edge = fix_edge(g.load(), "schema_patch", "schema_drift")
    assert edge["relation"] == "ADMISSIBLE" and edge["evidence"] ["rejections"] == 1
    assert edge["pitfalls"][-1] == "run-b: rejected by human review — Not this time"
    assert not any(change.startswith("pruned") for change in summary["changes"])


def test_verified_failure_counts_as_negative_evidence_but_tool_refusal_does_not():
    g = graph()
    failed = g.refine(run_record("verification_failed", RETRY_FIX, error_type="timeout", incident_id="run-4",
                                 verification={"job_id": "job_1", "status": "failed", "row_count": 0}))
    edge = fix_edge(g.load(), "retry_policy", "timeout")
    assert failed["committed"] and edge["relation"] == "PRUNED" and edge["evidence"]["failures"] == 1
    assert edge["pitfalls"] == ["run-4: applied but the rerun did not pass (status=failed, row_count=0)"]
    refused = g.refine(run_record("apply_failed", SCHEMA_FIX, error_type="timeout", incident_id="run-5",
                                  application={"applied": False, "message": "Only amount-to-float conversion is supported"}))
    edge = fix_edge(g.load(), "schema_patch", "timeout")
    assert refused["committed"] and edge["relation"] == "ADMISSIBLE" and edge["evidence"]["refusals"] == 1
    assert edge["pitfalls"] == ["run-5: tool did not confirm this change — Only amount-to-float conversion is supported"]


@pytest.mark.parametrize("event", ["critique_disagreed", "approval_tampered", "proposal_pruned", "budget_exhausted",
                                   "model_invalid", "model_error", "job_not_failed", "stage_error", None, 7])
def test_non_learning_outcomes_leave_the_file_untouched(event):
    g = graph()
    before = g.load()
    stamp = runtime_path("procedural_graph.json").stat().st_mtime_ns
    summary = g.refine(run_record(event, RETRY_FIX, approval={"approved": False, "human_note": "no"}))
    assert summary == {"event": event if isinstance(event, str) else None, "changed": False, "committed": False,
                       "revision": None, "changes": [], "reason": "Nothing to learn from this outcome"}
    assert g.load() == before
    assert runtime_path("procedural_graph.json").stat().st_mtime_ns == stamp


def test_refine_is_idempotent_per_incident_and_tolerates_bad_records():
    g = graph()
    assert g.refine(run_record("verified", SCHEMA_FIX, incident_id="run-6"))["committed"]
    again = g.refine(run_record("verified", SCHEMA_FIX, incident_id="run-6"))
    assert (again["changed"], again["committed"], again["revision"]) == (False, False, 1)
    assert fix_edge(g.load(), "schema_patch", "schema_drift")["evidence"]["successes"] == 2
    assert g.refine("not a record")["reason"] == "Run record must be a dict"
    bad_fix = run_record("verified", {"fix_type": "schema_patch", "target": "orders", "change": "x"})
    assert g.refine(bad_fix)["reason"] == "Proposal is not a valid fix"
    assert g.refine(run_record("verified", SCHEMA_FIX, error_type=None))["reason"] == "Nothing to learn from this outcome"
    assert g.load()["revision"] == 1


def test_failed_smoke_test_blocks_the_commit():
    g = graph(smoke=lambda: (False, "[FAIL] schema_drift: rerun reflects real fix"))
    summary = g.refine(run_record("verified", SCHEMA_FIX, incident_id="run-7"))
    assert summary["changed"] and not summary["committed"]
    assert summary["reason"].startswith("Smoke test failed; graph left unchanged: [FAIL]")
    assert g.load()["revision"] == 0
    assert fix_edge(g.load(), "schema_patch", "schema_drift")["evidence"]["successes"] == 1


def test_smoke_test_is_skipped_when_policy_disables_it():
    g = graph()
    data = g.load()
    data["policy"]["run_smoke_test_before_commit"] = False
    runtime_path("procedural_graph.json").write_text(json.dumps(data))
    calls = []
    g = graph(smoke=lambda: calls.append(1) or (False, "never consulted"))
    assert g.refine(run_record("verified", SCHEMA_FIX, incident_id="run-8"))["committed"]
    assert calls == []


def test_corrupt_runtime_graph_is_never_overwritten():
    path = runtime_path("procedural_graph.json")
    path.write_text("not JSON")
    g = graph()
    with pytest.raises(ValueError):
        g.localize("diagnose", "schema_drift")
    summary = g.refine(run_record("verified", SCHEMA_FIX))
    assert not summary["committed"] and summary["reason"] == "Graph not updated (JSONDecodeError)"
    assert path.read_text() == "not JSON"


def test_reset_restores_the_seed():
    g = graph()
    assert g.refine(run_record("verified", SCHEMA_FIX, incident_id="run-10"))["revision"] == 1
    assert g.reset() == 0
    assert g.load()["revision"] == 0 and g.load()["changelog"] == []


def test_concurrent_refinements_are_all_counted():
    g = graph()
    with ThreadPoolExecutor(4) as pool:
        summaries = list(pool.map(lambda i: g.refine(run_record("verified", SCHEMA_FIX, incident_id=f"run-{i}")), range(12)))
    assert all(s["committed"] for s in summaries)
    data = g.load()
    assert data["revision"] == 12
    assert fix_edge(data, "schema_patch", "schema_drift")["evidence"]["successes"] == 13


def test_bounds_are_enforced_and_prompt_views_stay_small():
    g = graph()
    for index in range(8):
        g.refine(run_record("rejected", SCHEMA_FIX, incident_id=f"rej-{index}",
                            approval={"approved": False, "human_note": "reason " + "x" * 500}))
    edge = fix_edge(g.load(), "schema_patch", "schema_drift")
    policy = g.load()["policy"]
    assert len(edge["pitfalls"]) == policy["max_pitfalls_per_edge"]
    assert all(len(p) <= procedural_graph.TEXT_LIMIT for p in edge["pitfalls"])
    assert edge["relation"] == "PRUNED"  # 8 rejections now outweigh the single seeded success
    view = g.localize("diagnose", "schema_drift")
    assert view["fast_path"] is None and view["pruned_fix_types"] == ["retry_policy", "schema_patch"]
    assert len(json.dumps(view["procedures"])) < procedural_graph.PROMPT_LIMIT


@pytest.mark.parametrize("mutate", [
    lambda d: d["edges"][2].update(relation="PRUNED"),
    lambda d: d["edges"][3].update(relation="ADMISSIBLE"),
    lambda d: d["edges"].append({"source": "diagnose", "target": "schema_patch", "relation": "ADMISSIBLE",
                                 "condition": {"error_type": ["schema_drift"]}}),
    lambda d: d["edges"].append({"source": "logs", "target": "schema_patch", "relation": "ADMISSIBLE"}),
    lambda d: d["edges"].append({"source": "schema", "target": "diagnose", "relation": "LEADS_TO"}),
    lambda d: d["edges"][0].update(condition={"error_type": "schema_drift"}),
    lambda d: d["edges"][0].update(guidance="g" * 301),
    lambda d: d["edges"][2]["evidence"].update(successes=-1),
    lambda d: d["edges"][2]["validated_fixes"].append({"incident_id": "x", "fix": RETRY_FIX}),
    lambda d: d["nodes"].append({"id": "hotfix", "kind": "fix"}),
    lambda d: d["policy"].update(prune_after_negatives=True),
    lambda d: d["policy"].pop("max_changelog_entries"),
    lambda d: d.update(extra=1),
])
def test_validate_graph_rejects_inconsistent_graphs(mutate):
    data = graph().load()
    mutate(data)
    with pytest.raises(ValueError):
        validate_graph(data)


def test_real_smoke_test_runner_passes_offline():
    passed, detail = procedural_graph.run_smoke_test(timeout=120)
    assert passed, detail
    assert "All checks passed." in detail
