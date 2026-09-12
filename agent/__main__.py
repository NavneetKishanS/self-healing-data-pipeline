"""Run real integrations: python -m agent check | run | serve."""

import argparse
import json
import sys

from .settings import integration_status, load_settings, positive_number


def main():
    parser = argparse.ArgumentParser(description="Live pipeline incident agent. No scripted AI fallback.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Show configuration status without revealing keys")
    check.add_argument("--model", action="store_true", help="Make one small real model request")
    run = commands.add_parser("run", help="Run the real model and configured integrations")
    run.add_argument("--job-id", default="job_1")
    run.add_argument("--inject-failure", choices=["schema_drift"])
    run.add_argument("--approval", choices=["console", "browser", "reject"], default="console")
    serve = commands.add_parser("serve", help="Serve authenticated AG-UI and REST endpoints")
    serve.add_argument("--local-no-auth", action="store_true", help="Explicit loopback-only development mode")
    serve.add_argument("--watch", action="store_true",
                       help="Investigate flagged ingestion batches automatically, within the daily model-call budget")
    args = parser.parse_args()
    load_settings()
    print("LIVE integrations (the repo pipeline itself is synthetic).", flush=True)
    if args.command == "check":
        print(json.dumps(integration_status(), indent=2))
        if args.model:
            from .model_client import LiveModel
            model = LiveModel()
            model.max_tokens = min(model.max_tokens, 256)
            try:
                reply = model(system="Reply with OK only.", prompt="Connection check.")
                print("Model connection: responded" if reply else "Model connection: empty response")
            except RuntimeError as exc:
                error_type = model.calls[-1].get("error_type", "unknown")
                print(f"Model connection: failed ({error_type}). {exc}")
                return 1
        return 0
    if args.command == "serve":
        from .server import create_app
        app = create_app(local_no_auth=args.local_no_auth, watch=args.watch)
        port = positive_number("AGENT_PORT", 8000, integer=True)
        print(f"Agent endpoint: http://127.0.0.1:{port}/agent", flush=True)
        print("Auth: LOCAL DEVELOPMENT ONLY" if args.local_no_auth else "Auth: Auth0 access tokens required", flush=True)
        if args.watch:
            status = app.extensions["watcher"].status()
            print(f"Watcher: investigating flagged batches every {status['interval_seconds']:g}s; "
                  f"model-call budget {status['budget']['used_today']}/{status['budget']['daily_limit']} used today", flush=True)
        app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)
        return 0

    from .live import run_live
    print(json.dumps(integration_status(), indent=2), flush=True)
    if args.approval == "browser":
        from memory_approval.approval_server import run_server_in_background, request_approval
        run_server_in_background()
        approval = request_approval
        print("Approval: Dev C's local Flask page at http://localhost:5050/ (not Auth0 protected)", flush=True)
    else:
        def approval(*, diagnosis, proposed_fix, confidence):
            print("\nDiagnosis: " + diagnosis)
            print("Proposed repair: " + json.dumps(proposed_fix))
            print("Critique confidence: " + str(confidence))
            try:
                decision = "reject" if args.approval == "reject" else input("Type approve to apply; anything else rejects: ").strip().lower()
            except EOFError:
                decision = "reject"
            return {"approved": decision == "approve", "human_note": "Console decision: " + decision}
    result = run_live(args.job_id, approval=approval, inject_failure=args.inject_failure,
                      notify=lambda event: print("[" + event["stage"] + "] " + event["status"], flush=True))
    print(json.dumps(result, indent=2))
    return 0 if result["outcome"] == "fixed" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError) as exc:
        print("Cannot start: " + str(exc), file=sys.stderr)
        sys.exit(2)
