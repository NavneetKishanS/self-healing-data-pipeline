"""Connect the workflow to Dev A/C functions and real service clients."""

from copy import deepcopy
import importlib.util
from threading import Lock
from uuid import uuid4

from .model_client import LiveModel
from .dataset import OrdersDataset
from .iris import IrisDataset
from .reporting import export_report
from .workflow import run_incident

_RUN_LOCK = Lock()  # The current pipeline and approval implementations share module state.


class _NarratedProcedures:
    """Forward to Dev C's procedural graph and report what it contributed, like tool events."""

    def __init__(self, graph, notify):
        self._graph, self._notify = graph, notify

    def localize(self, stage, error_type, proposal=None):
        view = self._graph.localize(stage, error_type, proposal)
        if self._notify:
            status = f"{stage} guidance from revision {view.get('revision')}"
            fast_path = view.get("fast_path") or {}
            if fast_path:
                status += f"; fast path {fast_path.get('fix_type')} ({fast_path.get('successes')} validated)"
            if view.get("pruned_fix_types"):
                status += "; pruned " + ", ".join(view["pruned_fix_types"])
            self._notify({"stage": "procedural_graph", "status": status})
        return view

    def refine(self, result):
        summary = self._graph.refine(result)
        if self._notify:
            if summary.get("committed"):
                status = f"refined to revision {summary.get('revision')}: " + "; ".join(summary.get("changes", []))
            else:
                status = "unchanged: " + str(summary.get("reason"))
            self._notify({"stage": "procedural_graph", "status": status})
        return summary


def run_live(job_id="job_1", *, approval=None, incident_id=None, inject_failure=None,
             report=True, notify=None, model=None, procedures=None):
    """Single-process, serialized live runs. No synthetic model fallback."""
    if not _RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("Another incident is running; shared pipeline/approval state is busy")
    try:
        dataset = IrisDataset(rows) if dataset_name == "iris" else OrdersDataset(inject_failure, rows=rows)
        from memory_approval import memory_store
        if procedures is None:
            from memory_approval.procedural_graph import ProceduralGraph
            procedures = ProceduralGraph()
        if approval is None:
            from memory_approval.approval_server import request_approval
            approval = request_approval
        selected_model = model or LiveModel()
        services = {}
        def call(name, fn):
            def wrapped(**kwargs):
                if notify:
                    notify({"stage": name, "status": "started"})
                value = fn(**kwargs)
                if notify:
                    event = {"stage": name, "status": "returned"}
                    if name in ("get_recent_logs", "rerun_pipeline"):
                        event["status"] = f"returned (pipeline: {value.get('status', 'unknown')})"
                    notify(event)
                return value
            return wrapped

        tools = {name: getattr(dataset, name) for name in
                 ("get_recent_logs", "get_schema", "apply_fix", "rerun_pipeline")}
        tools.update(search_past_incidents=memory_store.search_past_incidents,
                     log_incident=memory_store.log_incident,
                     request_approval=approval)
        result = run_incident(job_id, incident_id=incident_id or str(uuid4()),
                              tools={name: call(name, fn) for name, fn in tools.items()}, model=selected_model,
                              procedures=_NarratedProcedures(procedures, notify))
        result["model_requests"] = getattr(selected_model, "calls", [])
        result["integrations"] = services
        result["limitations"] = ["Local dataset batch; repairs are in-memory and verified against the expected schema. No production database is connected."]
        if report:
            # Prefer Dev C's exporter if it lands; otherwise use this isolated adapter.
            try:
                if importlib.util.find_spec("memory_approval.report_export"):
                    from memory_approval.report_export import report_export
                    exporter = report_export
                else:
                    exporter = export_report
                result["report"] = exporter(deepcopy(result))
            except Exception as exc:
                result["report"] = {"status": "unavailable", "error": type(exc).__name__, "url": None}
        else:
            result["report"] = {"status": "disabled", "url": None}
        return result
    finally:
        _RUN_LOCK.release()
