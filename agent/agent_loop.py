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
from agent.tool_registry import TOOL_SCHEMAS, get_dispatch_table

MAX_TOOL_CALLS = 8
MODEL = "claude-sonnet-4-6"  # swap freely, nothing else depends on this


def run_incident(job_id: str) -> dict:
    """
    Runs one full incident through the agent loop. Returns a terminal result dict:
    {"outcome": "fixed" | "needs_human" | "gave_up", "transcript": [...]}
    """
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
