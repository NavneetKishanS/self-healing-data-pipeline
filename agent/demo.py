"""Walk through the real orchestrator with a tiny fixture and scripted model answers."""

import argparse
import json

from .workflow import run_incident


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision", choices=["approve", "reject"], help="Skip interactive approval")
    parser.add_argument("--show-prompts", action="store_true", help="Print the rendered Jinja prompts")
    args = parser.parse_args()

    amounts = ["12.50", "8.00", "19.50"]
    memory = []
    fix = {"fix_type": "schema_patch", "target": "orders",
           "change": {"column": "amount", "new_type": "float"}}
    answers = iter([
        {"diagnosis": "The three order amounts arrived as strings; the demo expects numbers. Convert amount to float.",
         "proposed_fix": fix, "confidence": 0.9},
        {"agrees": True, "counter_argument": "Conversion would fail for nonnumeric text. These three fixture values are numeric strings.",
         "revised_confidence": 0.9},
    ])

    def pipeline():
        valid = all(type(amount) is float for amount in amounts)
        return {"job_id": "demo_orders", "status": "success" if valid else "failed",
                "row_count": len(amounts) if valid else 0}

    def get_recent_logs(job_id):
        print("1. INSPECT: The orders pipeline stopped because amount contains text.")
        return {**pipeline(), "error_type": "schema_drift", "affected_table": "orders",
                "error_message": "Expected float amounts, received strings: 12.50, 8.00, 19.50",
                "expected_row_count": 3, "timestamp": "2026-09-12T00:00:00Z"}

    def get_schema(table_name):
        print("2. SCHEMA: amount is currently string; the fixture requires float.")
        return {"table_name": table_name, "columns": [{"name": "amount", "type": "string",
                "nullable": False}], "last_changed": None}

    def search_past_incidents(error_type):
        print("3. MEMORY: No previous incidents in this fresh demo.")
        return {"matches": list(memory)}

    def model(*, system, prompt, **hints):
        if args.show_prompts:
            print("\n--- Rendered Jinja request ---\n" + system + "\n" + prompt + "\n--- End request ---")
        answer = next(answers)
        if "diagnosis" in answer:
            print("4. DIAGNOSIS [scripted model]: " + answer["diagnosis"])
        else:
            print("5. CRITIQUE [scripted model]: " + answer["counter_argument"])
        return json.dumps(answer)

    def request_approval(diagnosis, proposed_fix, confidence):
        print("6. APPROVAL: Proposed change: " + json.dumps(proposed_fix))
        if args.decision is None:
            try:
                choice = input("   Type approve to apply it; anything else rejects: ").strip().lower()
            except EOFError:
                choice = "reject"
        else:
            choice = args.decision
            print("   Command-line decision: " + choice)
        return {"approved": choice == "approve", "human_note": "Local demo decision: " + choice}

    def apply_fix(fix):
        expected = {"fix_type": "schema_patch", "target": "orders",
                    "change": {"column": "amount", "new_type": "float"}}
        if fix != expected:
            return {"applied": False, "message": "Unsupported demo repair"}
        amounts[:] = [float(amount) for amount in amounts]
        print("7. APPLY: Converted the three in-memory amounts to numbers.")
        return {"applied": True, "message": "Converted fixture amounts"}

    def rerun_pipeline(job_id):
        result = pipeline()
        print(f"8. VERIFY: {result['status']}; {result['row_count']} of 3 orders processed.")
        return result

    def log_incident(incident):
        memory.append(incident)
        print("9. RECORD: Saved the resolution in this demo's in-memory incident list.")
        return {"logged": True}

    print("LOCAL DEMO: real orchestration; simulated pipeline and scripted AI. No API calls or saved data.\n")
    print("Before: " + repr(amounts) + " — pipeline " + pipeline()["status"] + "\n")
    result = run_incident("demo_orders", tools={
        "get_recent_logs": get_recent_logs, "get_schema": get_schema,
        "search_past_incidents": search_past_incidents, "request_approval": request_approval,
        "apply_fix": apply_fix, "rerun_pipeline": rerun_pipeline, "log_incident": log_incident,
    }, model=model)
    print("\nOutcome: " + result["outcome"] + " — " + result["reason"])
    print("After:  " + repr(amounts) + " — pipeline " + pipeline()["status"])
    print(f"Calls: {result['tool_calls']} tools, {result['model_calls']} scripted model responses")


if __name__ == "__main__":
    main()
