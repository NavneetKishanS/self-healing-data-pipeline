"""
Dev C owns this file.

Procedural graph: the actionable counterpart of incidents.json. Incident memory records what
happened; this graph records which repair transitions are admissible or pruned for an error
type, why, and what the rerun must prove. The orchestrator reads a 2-hop neighbourhood into its
two prompts, then hands its terminal run record to `refine`, which rewrites edges offline under
the same locked JSON transaction memory uses. No model is called here: the refiner is a fixed
set of rules over human decisions and verified outcomes, so the 3-model-call budget is untouched.

Relations are derived, never asserted: an edge into a fix node is PRUNED exactly when its
negative evidence (human rejections + verified failures) reaches the policy threshold and
outweighs its successes. `validate_graph` rejects any graph that says otherwise.
"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

from memory_approval.storage import runtime_path, transaction
from memory_approval.validation import validate_fix

SEED_PATH = Path(__file__).with_name("procedural_graph_seed.json")
REPO_ROOT = Path(__file__).resolve().parents[1]
RELATIONS = ("LEADS_TO", "ADMISSIBLE", "PRUNED")
FIX_TYPES = ("schema_patch", "config_change", "retry_policy")
NODE_KINDS = ("evidence", "reasoning", "gate", "fix", "verification")
STAGES = ("diagnose", "critique")
# What the orchestrator's terminal_event teaches. Everything else (budget exhaustion, invalid
# model output, tampered approvals, a critique that merely disagreed) leaves the graph alone.
LEARNING = {"verified": "success", "rejected": "rejection",
            "verification_failed": "failure", "apply_failed": "refusal"}
POLICY = {
    "prune_after_negatives": 1, "fast_path_min_successes": 1, "run_smoke_test_before_commit": True,
    "max_pitfalls_per_edge": 5, "max_validated_fixes_per_edge": 3, "max_incidents_per_edge": 20,
    "max_changelog_entries": 50,
}
TEXT_LIMIT = 300
NOTE_LIMIT = 240
PROMPT_LIMIT = 8000  # characters of JSON any single localized view may occupy in a prompt
GRAPH_LIMIT = 200_000
VERIFICATION_GUIDANCE = "Verification passes only when the rerun reports success and row_count equals expected_row_count."


def _clean(value, limit=TEXT_LIMIT):
    """Human notes and tool messages become prompt text later: strip control characters and secrets."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value if value is not None else ""))
    text = re.sub(r"(?i)bearer\s+\S+|sk-[\w-]{12,}", "[REDACTED]", text)
    return " ".join(text.split())[:limit]


def _empty_evidence():
    return {"successes": 0, "rejections": 0, "failures": 0, "refusals": 0, "incidents": []}


def _evidence(edge):
    return edge.get("evidence") or _empty_evidence()


def _matches(edge, error_type):
    types = edge.get("condition", {}).get("error_type")
    return types is None or error_type in types


def _derived_relation(evidence, policy):
    negatives = evidence["rejections"] + evidence["failures"]
    pruned = negatives >= policy["prune_after_negatives"] and negatives > evidence["successes"]
    return "PRUNED" if pruned else "ADMISSIBLE"


def _validate_edge(edge, kinds, policy):
    allowed = {"source", "target", "relation", "condition", "guidance", "pitfalls", "evidence", "validated_fixes"}
    if not isinstance(edge, dict) or not {"source", "target", "relation"} <= set(edge) or not set(edge) <= allowed:
        raise ValueError("Edge keys must be source, target, relation and optional condition, guidance, pitfalls, evidence, validated_fixes")
    if edge["source"] not in kinds or edge["target"] not in kinds or edge["source"] == edge["target"]:
        raise ValueError("Edge endpoints must be distinct known nodes")
    if edge["relation"] not in RELATIONS:
        raise ValueError("Unsupported edge relation")
    into_fix = kinds[edge["target"]] == "fix"
    if into_fix != (edge["relation"] in ("ADMISSIBLE", "PRUNED")) or (into_fix and edge["source"] != "diagnose"):
        raise ValueError("ADMISSIBLE/PRUNED apply exactly to edges from diagnose into fix nodes")
    condition = edge.get("condition", {})
    if not isinstance(condition, dict) or set(condition) - {"error_type"}:
        raise ValueError("Condition supports only error_type")
    if "error_type" in condition:
        types = condition["error_type"]
        if (not isinstance(types, list) or not types or len(set(types)) != len(types)
                or not all(isinstance(t, str) and t.strip() and len(t) <= 200 for t in types)):
            raise ValueError("Condition error_type must be a list of distinct nonempty strings")
    if not isinstance(edge.get("guidance", ""), str) or len(edge.get("guidance", "")) > TEXT_LIMIT:
        raise ValueError("Guidance must be a short string")
    pitfalls = edge.get("pitfalls", [])
    if (not isinstance(pitfalls, list) or len(pitfalls) > policy["max_pitfalls_per_edge"]
            or not all(isinstance(p, str) and p.strip() and len(p) <= TEXT_LIMIT for p in pitfalls)):
        raise ValueError("Pitfalls must be a bounded list of short strings")
    evidence = _evidence(edge)
    if not isinstance(evidence, dict) or set(evidence) != set(_empty_evidence()):
        raise ValueError("Evidence keys must be successes, rejections, failures, refusals, incidents")
    if any(type(evidence[k]) is not int or evidence[k] < 0 for k in ("successes", "rejections", "failures", "refusals")):
        raise ValueError("Evidence counters must be non-negative integers")
    incidents = evidence["incidents"]
    if (not isinstance(incidents, list) or len(incidents) > policy["max_incidents_per_edge"]
            or not all(isinstance(i, str) and i.strip() and len(i) <= 200 for i in incidents)):
        raise ValueError("Evidence incidents must be a bounded list of ids")
    fixes = edge.get("validated_fixes", [])
    if not isinstance(fixes, list) or len(fixes) > policy["max_validated_fixes_per_edge"] or (fixes and not into_fix):
        raise ValueError("validated_fixes must be a bounded list on a fix edge")
    for item in fixes:
        if not isinstance(item, dict) or set(item) != {"incident_id", "fix"} or not isinstance(item["incident_id"], str):
            raise ValueError("Each validated fix needs incident_id and fix")
        if validate_fix(item["fix"])["fix_type"] != edge["target"]:
            raise ValueError("Validated fix type must match the edge target")
    if into_fix and edge["relation"] != _derived_relation(evidence, policy):
        raise ValueError("Edge relation must be derived from its evidence and the policy")


def validate_graph(graph):
    """Raise ValueError unless the graph is well-formed, bounded, and self-consistent."""
    if not isinstance(graph, dict) or set(graph) != {"version", "revision", "policy", "nodes", "edges", "changelog"}:
        raise ValueError("Graph requires exactly version, revision, policy, nodes, edges, changelog")
    if graph["version"] != 1 or type(graph["revision"]) is not int or graph["revision"] < 0:
        raise ValueError("Unsupported graph version or revision")
    policy = graph["policy"]
    if not isinstance(policy, dict) or set(policy) != set(POLICY):
        raise ValueError("Policy keys must be exactly " + ", ".join(sorted(POLICY)))
    for key, default in POLICY.items():
        if type(policy[key]) is not type(default) or (type(default) is int and policy[key] < 0):
            raise ValueError(f"Policy {key} must be a non-negative {type(default).__name__}")
    kinds = {}
    if not isinstance(graph["nodes"], list) or not graph["nodes"]:
        raise ValueError("Graph nodes must be a nonempty list")
    for node in graph["nodes"]:
        if (not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]
                or node["id"] in kinds or node.get("kind") not in NODE_KINDS):
            raise ValueError("Each node needs a unique string id and a supported kind")
        if (node["kind"] == "fix") != (node["id"] in FIX_TYPES):
            raise ValueError("Fix nodes must be exactly the CONTEXT.md fix types")
        kinds[node["id"]] = node["kind"]
    if not {"diagnose", "rerun"} <= set(kinds) or not isinstance(graph["edges"], list):
        raise ValueError("Graph needs diagnose and rerun nodes and an edge list")
    scopes = {}
    for edge in graph["edges"]:
        _validate_edge(edge, kinds, policy)
        scope = frozenset(edge.get("condition", {}).get("error_type", [])) or None
        for other in scopes.setdefault((edge["source"], edge["target"]), []):
            if other is None or scope is None or other & scope:
                raise ValueError("Edges between the same nodes must have disjoint conditions")
        scopes[(edge["source"], edge["target"])].append(scope)
    if not isinstance(graph["changelog"], list) or len(graph["changelog"]) > policy["max_changelog_entries"]:
        raise ValueError("Changelog must be a bounded list")
    if len(json.dumps(graph, allow_nan=False)) > GRAPH_LIMIT:
        raise ValueError("Graph is too large")


def run_smoke_test(timeout=60):
    """Dev A's offline pipeline check, in a fresh process so shared module state stays untouched."""
    try:
        completed = subprocess.run([sys.executable, "-B", "-m", "pipeline.smoke_test"], cwd=REPO_ROOT,
                                   capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, type(exc).__name__
    return completed.returncode == 0, (completed.stdout + completed.stderr)[-500:]


def _neighborhood(edges, node, error_type, hops):
    """Edges into `node` plus edges reachable from it within `hops`, in discovery order."""
    matching = [edge for edge in edges if _matches(edge, error_type)]
    selected = [edge for edge in matching if edge["target"] == node]
    frontier, seen = [node], {node}
    for _ in range(hops):
        following = []
        for current in frontier:
            for edge in matching:
                if edge["source"] == current and not any(edge is chosen for chosen in selected):
                    selected.append(edge)
                    if edge["target"] not in seen:
                        seen.add(edge["target"])
                        following.append(edge["target"])
        frontier = following
    return selected


def _transition(edge):
    view = {"step": edge["source"] + " -> " + edge["target"]}
    if edge["relation"] != "LEADS_TO":
        view["status"] = edge["relation"].lower()
    if edge.get("guidance"):
        view["guidance"] = edge["guidance"]
    if edge.get("pitfalls"):
        view["pitfalls"] = list(edge["pitfalls"])
    if edge["relation"] != "LEADS_TO":
        evidence = _evidence(edge)
        view["evidence"] = {key: evidence[key] for key in ("successes", "rejections", "failures", "refusals")}
        if edge.get("validated_fixes"):
            view["validated_fixes"] = [{"target": item["fix"]["target"], "change": deepcopy(item["fix"]["change"])}
                                       for item in edge["validated_fixes"]]
    return view


def _localize(graph, stage, error_type, proposal=None):
    policy = graph["policy"]
    fix_edges = [edge for edge in graph["edges"]
                 if edge["source"] == "diagnose" and edge["relation"] != "LEADS_TO" and _matches(edge, error_type)]
    validated = [edge for edge in fix_edges if edge["relation"] == "ADMISSIBLE"
                 and _evidence(edge)["successes"] >= policy["fast_path_min_successes"]]
    fast_path = None
    if validated:
        best = max(validated, key=lambda edge: _evidence(edge)["successes"])
        fast_path = {"fix_type": best["target"], "successes": _evidence(best)["successes"]}
        if best.get("validated_fixes"):
            latest = best["validated_fixes"][-1]["fix"]
            fast_path["validated_fix"] = {"target": latest["target"], "change": deepcopy(latest["change"])}
    if stage == "diagnose":
        edges = _neighborhood(graph["edges"], "diagnose", error_type, hops=2)
    else:
        fix_type = proposal.get("fix_type") if isinstance(proposal, dict) else None
        edges = _neighborhood(graph["edges"], fix_type, error_type, hops=1) if fix_type in FIX_TYPES else []
    procedures = {"error_type": error_type, "transitions": [_transition(edge) for edge in edges]}
    if fast_path:
        procedures["fast_path"] = fast_path
    if stage == "critique":
        status = next((edge["relation"].lower() for edge in fix_edges if edge["target"] == fix_type), "unknown")
        procedures["proposal"] = {"fix_type": fix_type, "status": status}
    return {
        "revision": graph["revision"], "procedures": procedures, "fast_path": fast_path,
        "pruned_fix_types": sorted(edge["target"] for edge in fix_edges if edge["relation"] == "PRUNED"),
    }


def _add_pitfall(edge, text, policy, changes):
    pitfalls = edge.setdefault("pitfalls", [])
    if text in pitfalls:
        return
    pitfalls.append(text)
    del pitfalls[:-policy["max_pitfalls_per_edge"]]
    changes.append("pitfall recorded on diagnose -> " + edge["target"] + ": " + text)


def _learn(graph, kind, error_type, fix, incident_id, result):
    """Apply one outcome to the candidate graph; return human-readable change descriptions."""
    policy, fix_type, changes = graph["policy"], fix["fix_type"], []
    edge = next((edge for edge in graph["edges"] if edge["source"] == "diagnose"
                 and edge["target"] == fix_type and _matches(edge, error_type)), None)
    if edge is None:
        edge = {"source": "diagnose", "target": fix_type, "relation": "ADMISSIBLE",
                "condition": {"error_type": [error_type]}, "evidence": _empty_evidence()}
        graph["edges"].append(edge)
        changes.append(f"added diagnose -> {fix_type} for {error_type}")
    evidence = edge.setdefault("evidence", _empty_evidence())
    if incident_id in evidence["incidents"]:
        return []  # Already learned from this incident; refinement is idempotent per incident.
    evidence["incidents"].append(incident_id)
    del evidence["incidents"][:-policy["max_incidents_per_edge"]]
    if kind == "success":
        evidence["successes"] += 1
        changes.append(f"reinforced diagnose -> {fix_type} for {error_type} ({evidence['successes']} validated)")
        fixes = [item for item in edge.get("validated_fixes", []) if item["fix"] != fix]
        fixes.append({"incident_id": incident_id, "fix": deepcopy(fix)})
        edge["validated_fixes"] = fixes[-policy["max_validated_fixes_per_edge"]:]
        if not edge.get("guidance"):
            edge["guidance"] = _clean(f"Validated on {incident_id}: {fix_type} on {fix['target']} with "
                                      + json.dumps(fix["change"], sort_keys=True))
        if not any(e["source"] == fix_type and e["target"] == "rerun" for e in graph["edges"]):
            graph["edges"].append({"source": fix_type, "target": "rerun", "relation": "LEADS_TO",
                                   "guidance": VERIFICATION_GUIDANCE})
            changes.append(f"added {fix_type} -> rerun")
    elif kind == "rejection":
        evidence["rejections"] += 1
        note = _clean((result.get("approval") or {}).get("human_note"), NOTE_LIMIT) or "no note given"
        _add_pitfall(edge, f"{incident_id}: rejected by human review — {note}", policy, changes)
    elif kind == "failure":
        evidence["failures"] += 1
        verification = result.get("verification") or {}
        _add_pitfall(edge, f"{incident_id}: applied but the rerun did not pass (status={_clean(verification.get('status'), 40)}, "
                     f"row_count={_clean(verification.get('row_count'), 40)})", policy, changes)
    elif kind == "refusal":
        evidence["refusals"] += 1
        message = _clean((result.get("application") or {}).get("message"), NOTE_LIMIT) or "no message"
        _add_pitfall(edge, f"{incident_id}: tool did not confirm this change — {message}", policy, changes)
    relation = _derived_relation(evidence, policy)
    if relation != edge["relation"]:
        edge["relation"] = relation
        changes.append(f"{'pruned' if relation == 'PRUNED' else 'restored'} diagnose -> {fix_type} for {error_type}")
    return changes


def _dry_run(graph):
    """Every view the orchestrator could request must render and stay small enough for a prompt."""
    error_types = {t for edge in graph["edges"] for t in edge.get("condition", {}).get("error_type", [])}
    for error_type in error_types or {"unknown"}:
        views = [_localize(graph, "diagnose", error_type)]
        views += [_localize(graph, "critique", error_type, {"fix_type": fix_type}) for fix_type in FIX_TYPES]
        for view in views:
            if len(json.dumps(view["procedures"], allow_nan=False)) > PROMPT_LIMIT:
                raise ValueError("Localized procedures would exceed the prompt budget")


class ProceduralGraph:
    """Seeded, validated, atomically refined procedural memory. Safe to construct per run."""

    def __init__(self, path=None, seed_path=SEED_PATH, smoke_test=run_smoke_test):
        self.path = Path(path) if path else runtime_path("procedural_graph.json")
        self.seed_path = Path(seed_path)
        self.smoke_test = smoke_test

    def _seed(self):
        with self.seed_path.open(encoding="utf-8") as source:
            graph = json.load(source)
        validate_graph(graph)
        return graph

    def load(self) -> dict:
        with transaction(self.path, self._seed) as graph:
            validate_graph(graph)
            return deepcopy(graph)

    def reset(self) -> int:
        seed = self._seed()
        with transaction(self.path, self._seed) as graph:
            graph.clear()
            graph.update(seed)
        return seed["revision"]

    def localize(self, stage: str, error_type: str, proposal: dict = None) -> dict:
        """The 2-hop neighbourhood the orchestrator renders into one prompt.

        Returns {"revision", "procedures", "fast_path", "pruned_fix_types"}; only `procedures`
        reaches the model. `pruned_fix_types` is the Python-side guard input.
        """
        if stage not in STAGES:
            raise ValueError("stage must be diagnose or critique")
        if not isinstance(error_type, str) or not error_type.strip():
            raise ValueError("error_type must be nonempty")
        return _localize(self.load(), stage, error_type, proposal)

    def refine(self, result: dict) -> dict:
        """Learn from one terminal run record; commit only if the candidate graph passes every gate.

        Returns {"event", "changed", "committed", "revision", "changes", "reason"}. Never raises
        for a malformed record or a corrupt file: the graph is simply left as it was.
        """
        summary = {"event": None, "changed": False, "committed": False, "revision": None,
                   "changes": [], "reason": None}
        if not isinstance(result, dict):
            summary["reason"] = "Run record must be a dict"
            return summary
        event = result.get("terminal_event")
        kind = LEARNING.get(event)
        error_type, incident_id = result.get("error_type"), result.get("incident_id")
        fix = (result.get("diagnosis") or {}).get("proposed_fix") if isinstance(result.get("diagnosis"), dict) else None
        summary["event"] = event if isinstance(event, str) else None
        if (kind is None or not isinstance(error_type, str) or not error_type.strip()
                or not isinstance(incident_id, str) or not incident_id.strip()):
            summary["reason"] = "Nothing to learn from this outcome"
            return summary
        try:
            fix = validate_fix(fix)
        except (ValueError, TypeError):
            summary["reason"] = "Proposal is not a valid fix"
            return summary
        try:
            with transaction(self.path, self._seed) as graph:
                validate_graph(graph)
                summary["revision"] = graph["revision"]
                candidate = deepcopy(graph)
                changes = _learn(candidate, kind, error_type, fix, incident_id, result)
                if not changes:
                    summary["reason"] = "Graph already reflects this incident"
                    return summary
                summary["changed"] = True
                candidate["revision"] += 1
                candidate["changelog"].append({
                    "revision": candidate["revision"], "incident_id": incident_id, "error_type": error_type,
                    "event": event, "changes": changes,
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
                del candidate["changelog"][:-candidate["policy"]["max_changelog_entries"]]
                validate_graph(candidate)
                _dry_run(candidate)
                if candidate["policy"]["run_smoke_test_before_commit"]:
                    passed, detail = self.smoke_test()
                    if passed is not True:
                        summary["reason"] = "Smoke test failed; graph left unchanged: " + _clean(detail, 200)
                        return summary
                graph.clear()
                graph.update(candidate)
                summary.update(committed=True, revision=candidate["revision"], changes=changes)
        except (ValueError, TypeError, OSError) as exc:
            summary["reason"] = f"Graph not updated ({type(exc).__name__})"
        return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect or reset the procedural graph.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="Admissible and pruned repairs per error type, plus recent changes")
    explain = commands.add_parser("explain", help="Print the procedural memory the model would receive")
    explain.add_argument("--error-type", required=True)
    explain.add_argument("--fix-type", choices=FIX_TYPES, help="Also print the critique view for this proposal")
    commands.add_parser("reset", help="Restore the runtime graph from the seed")
    args = parser.parse_args(argv)
    graph = ProceduralGraph()
    if args.command == "reset":
        print(f"Restored {graph.path} to seed revision {graph.reset()}")
        return 0
    if args.command == "explain":
        print(json.dumps(graph.localize("diagnose", args.error_type)["procedures"], indent=2))
        if args.fix_type:
            print(json.dumps(graph.localize("critique", args.error_type, {"fix_type": args.fix_type})["procedures"], indent=2))
        return 0
    data = graph.load()
    print(f"Procedural graph revision {data['revision']} ({graph.path})")
    for edge in data["edges"]:
        if edge["source"] != "diagnose" or edge["relation"] == "LEADS_TO":
            continue
        evidence = _evidence(edge)
        scope = ", ".join(edge.get("condition", {}).get("error_type", ["any error type"]))
        print(f"  [{edge['relation']:<10}] {scope}: {edge['target']}  successes={evidence['successes']} "
              f"rejections={evidence['rejections']} failures={evidence['failures']} refusals={evidence['refusals']}")
        for pitfall in edge.get("pitfalls", []):
            print("      pitfall: " + pitfall)
    print("Recent changes:" if data["changelog"] else "No changes since the seed.")
    for entry in data["changelog"][-10:]:
        print(f"  r{entry['revision']} {entry['at']} {entry['incident_id']} ({entry['event']}): " + "; ".join(entry["changes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
