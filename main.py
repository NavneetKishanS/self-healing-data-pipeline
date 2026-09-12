"""
Integration entry point. Not owned by any one dev — this is where the three branches
meet. Runs one full incident end to end: injects a failure, runs the agent loop, prints
the outcome.

Usage:
    python main.py --inject-failure schema_drift
    python main.py --inject-failure schema_drift --real   # use real tools, not stubs
"""

import argparse
from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inject-failure", choices=["schema_drift", "null_spike", "timeout"], required=True)
    parser.add_argument("--job-id", default="job_1")
    parser.add_argument("--real", action="store_true", help="Use real tool implementations instead of stubs.")
    args = parser.parse_args()

    if args.real:
        import agent.tool_registry as tool_registry
        tool_registry.USE_STUBS = False

        from memory_approval.approval_server import run_server_in_background
        run_server_in_background()

    from pipeline.failures import inject
    inject(args.inject_failure, args.job_id)
    print(f"[main] Injected failure: {args.inject_failure} on {args.job_id}")

    from agent.agent_loop import run_incident
    result = run_incident(args.job_id)

    print("\n" + "=" * 60)
    print(f"OUTCOME: {result['outcome']}")
    if "reason" in result:
        print(f"REASON: {result['reason']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
