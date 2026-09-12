"""
Dev B owns this file.

The ReAct-style loop: call tools, observe results, reason, repeat, until the model
reports a terminal state or the tool-call budget is exhausted. Uses Anthropic's
tool-calling API. Swap the client/model lines if your team prefers a different provider —
nothing else here is provider-specific.
"""

import os
import json
import anthropic

from agent.prompts import SYSTEM_PROMPT, CRITIQUE_INSTRUCTION
from agent import tool_registry
from agent.tool_registry import TOOL_SCHEMAS, get_dispatch_table

MAX_TOOL_CALLS = 8
MODEL = "claude-sonnet-4-6"  # swap freely, nothing else depends on this


def _make_stub_tool_call(tool_name: str, tool_input: dict) -> dict:
    """Generates a stub tool-use block for synthetic responses."""
    return {
        "type": "tool_use",
        "id": f"stub-{tool_name}-{id(tool_input)}",
        "name": tool_name,
        "input": tool_input,
    }


def _run_stub_incident(job_id: str) -> dict:
    """
    Runs incident using stub tools without calling the real API.
    Generates a synthetic agent reasoning sequence.
    """
    dispatch = get_dispatch_table()

    messages = [{
        "role": "user",
        "content": f"Job {job_id} has failed. Diagnose the issue and fix it following your rules.",
    }]

    # Synthetic agent reasoning: gather evidence, diagnose, critique, request approval, apply, rerun, log
    tool_calls_made = 0

    # 1. Get recent logs
    tool_calls_made += 1
    result = dispatch["get_recent_logs"](job_id)
    print(f"[tool] get_recent_logs({job_id}) -> {result}")
    error_type = result.get("error_type", "unknown")
    affected_table = result.get("affected_table", "orders")

    # 2. Get schema
    tool_calls_made += 1
    result = dispatch["get_schema"](affected_table)
    print(f"[tool] get_schema({affected_table}) -> {result}")

    # 3. Search past incidents
    tool_calls_made += 1
    result = dispatch["search_past_incidents"](error_type)
    print(f"[tool] search_past_incidents({error_type}) -> {result}")
    past_matches = result.get("matches", [])

    # Synthesize diagnosis and proposed fix from evidence
    diagnosis = f"Pipeline failed due to {error_type}. "
    if past_matches:
        diagnosis += f"Similar incident occurred before, last fix: {past_matches[0].get('fix_applied', {})}"

    proposed_fix = {
        "fix_type": "schema_patch" if error_type == "schema_drift" else "config_change",
        "target": affected_table,
        "change": {"column": "amount", "new_type": "float"} if error_type == "schema_drift" else {},
    }
    confidence = 0.8

    print(f"[agent] Diagnosis: {diagnosis}")
    print(f"[agent] Proposed fix: {proposed_fix} (confidence: {confidence})")
    print(f"[agent] Critiquing own diagnosis...")

    # 4. Request approval
    tool_calls_made += 1
    result = dispatch["request_approval"](diagnosis, proposed_fix, confidence)
    print(f"[tool] request_approval(...) -> {result}")

    if not result.get("approved"):
        return {"outcome": "needs_human", "transcript": messages,
                "reason": "Human rejected the proposed fix."}

    # 5. Apply fix
    tool_calls_made += 1
    result = dispatch["apply_fix"](proposed_fix)
    print(f"[tool] apply_fix({proposed_fix}) -> {result}")

    if not result.get("applied"):
        return {"outcome": "gave_up", "transcript": messages,
                "reason": f"Failed to apply fix: {result.get('message')}"}

    # 6. Rerun pipeline
    tool_calls_made += 1
    result = dispatch["rerun_pipeline"](job_id)
    print(f"[tool] rerun_pipeline({job_id}) -> {result}")

    if result.get("status") != "success":
        return {"outcome": "needs_human", "transcript": messages,
                "reason": "Pipeline still failed after fix was applied."}

    # 7. Log incident
    tool_calls_made += 1
    incident = {
        "incident_id": f"auto-{job_id}-{int(__import__('time').time())}",
        "error_type": error_type,
        "root_cause": diagnosis,
        "fix_applied": proposed_fix,
        "outcome": "resolved",
    }
    result = dispatch["log_incident"](incident)
    print(f"[tool] log_incident(...) -> {result}")

    return {"outcome": "fixed", "transcript": messages, "tool_calls_made": tool_calls_made}


def run_incident(job_id: str) -> dict:
    """
    Runs one full incident through the agent loop. Returns a terminal result dict:
    {"outcome": "fixed" | "needs_human" | "gave_up", "transcript": [...]}
    """
    # In stub mode, use synthetic reasoning to avoid API calls
    if tool_registry.USE_STUBS:
        return _run_stub_incident(job_id)

    # Real mode: use actual API
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    dispatch = get_dispatch_table()

    messages = [{
        "role": "user",
        "content": f"Job {job_id} has failed. Diagnose the issue and fix it following your rules.",
    }]

    tool_calls_made = 0
    critique_done = False

    while tool_calls_made < MAX_TOOL_CALLS:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        # Print any narration text as it comes in — this is what the demo audience sees.
        for block in response.content:
            if block.type == "text":
                print(f"[agent] {block.text}")

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_use_blocks:
            # Model produced only text — treat as a terminal report if it stopped naturally.
            if response.stop_reason == "end_turn":
                return {"outcome": "gave_up", "transcript": messages}
            break

        tool_results = []
        should_nudge_critique = False
        for block in tool_use_blocks:
            tool_calls_made += 1
            fn = dispatch.get(block.name)
            if fn is None:
                result = {"error": f"Unknown tool {block.name}"}
            else:
                result = fn(**block.input)

            print(f"[tool] {block.name}({block.input}) -> {result}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

            # Nudge the model to run the critique step exactly once, right after it has
            # gathered enough evidence to propose a fix (heuristic: after search_past_incidents).
            if block.name == "search_past_incidents" and not critique_done:
                critique_done = True
                should_nudge_critique = True

            if tool_calls_made >= MAX_TOOL_CALLS:
                break

        # Critique nudge is a separate text block in the same user turn — tool_result
        # blocks must pair 1:1 with tool_use blocks, so this cannot itself be a tool_result.
        if should_nudge_critique:
            tool_results.append({"type": "text", "text": CRITIQUE_INSTRUCTION})

        messages.append({"role": "user", "content": tool_results})

    return {"outcome": "gave_up", "transcript": messages,
            "reason": "Exceeded max tool call budget without reaching a terminal state."}
