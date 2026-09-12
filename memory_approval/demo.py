"""Local approval demo with explicit fixture data; does not run or claim a repair."""
import argparse
import json
import os
import uuid
from dotenv import load_dotenv
from memory_approval.approval_server import request_approval, run_server_in_background
from memory_approval.approvals import approval_context


def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--external-server", action="store_true")
    args = parser.parse_args()
    os.environ["APPROVAL_TIMEOUT_SECONDS"] = str(args.timeout)
    server = None if args.external_server else run_server_in_background(args.port)
    try:
        print("DEMO FIXTURE: approval transport only. No model call or pipeline mutation.")
        with approval_context("demo-" + uuid.uuid4().hex[:12]):
            result = request_approval(
                "The amount column changed from float to string.",
                {"fix_type": "schema_patch", "target": "orders",
                 "change": {"column": "amount", "new_type": "float"}},
                0.88)
        print(json.dumps(result, indent=2))
    finally:
        if server:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
