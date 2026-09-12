"""Connect the workflow to Dev A/C functions and real service clients."""

from copy import deepcopy
import importlib.util
from threading import Lock
from uuid import uuid4

from .model_client import LiveModel
from .research_tools import search_repair_docs
from .reporting import export_report
from .workflow import run_incident

_RUN_LOCK = Lock()  # The current pipeline and approval implementations share module state.


def run_live(job_id="job_1", *, approval=None, incident_id=None, inject_failure=None,
             report=True, notify=None, model=None):
    """Single-process, serialized live runs. No synthetic model fallback."""
    if not _RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("Another incident is running; shared pipeline/approval state is busy")
    try:
        from pipeline import tools_pipeline
        from memory_approval import memory_store
        if approval is None:
            from memory_approval.approval_server import request_approval
            approval = request_approval
        selected_model = model or LiveModel()
        if inject_failure:
            from pipeline.failures import inject
            from pipeline.synthetic_pipeline import reset
            reset(job_id)
            inject(inject_failure, job_id)

        def call(name, fn):
            def wrapped(**kwargs):
                if notify:
                    notify({"stage": name, "status": "started"})
                value = fn(**kwargs)
                if notify:
                    notify({"stage": name, "status": "returned"})
                return value
            return wrapped

        tools = {name: getattr(tools_pipeline, name) for name in
                 ("get_recent_logs", "get_schema", "apply_fix", "rerun_pipeline")}
        tools.update(search_past_incidents=memory_store.search_past_incidents,
                     log_incident=memory_store.log_incident,
                     request_approval=approval, search_repair_docs=search_repair_docs)
        # Tighten the schema-drift demo at the adapter boundary. Do not edit Dev A's code.
        # Cache evidence from counted calls; do not perform hidden extra tool calls.
        original_apply = tools["apply_fix"]
        original_logs = tools["get_recent_logs"]
        observed_logs = {}

        def capture_logs(job_id):
            observed_logs.update(original_logs(job_id))
            return deepcopy(observed_logs)

        def checked_apply(fix):
            if observed_logs.get("error_type") in ("schema_drift", "schema_mismatch"):
                expected = {"fix_type": "schema_patch", "target": observed_logs["affected_table"],
                            "change": {"column": "amount", "new_type": "float"}}
                if fix != expected:
                    return {"applied": False, "message": "Current demo adapter supports only the known amount-to-float schema repair"}
                return original_apply(deepcopy(fix))
            return {"applied": False, "message": "Other scenarios require Dev A's validated mutation implementation"}

        tools.update(apply_fix=checked_apply, get_recent_logs=capture_logs)
        result = run_incident(job_id, incident_id=incident_id or str(uuid4()),
                              tools={name: call(name, fn) for name, fn in tools.items()}, model=selected_model)
        result["model_requests"] = getattr(selected_model, "calls", [])
        result["limitations"] = ["Pipeline is synthetic and rerun resets failure state; reported success is not independent proof of repair correctness"]
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
