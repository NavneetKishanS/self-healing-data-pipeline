# Dev C — memory, approval, and optional integrations

Dev C owns `memory_approval/` and the local approval frontend. The required path remains JSON
memory plus the Flask approval page. CopilotKit, Auth0, and Ambiguous are optional and cannot
disable that fallback.

## What is implemented

- Immutable seeded incident history plus atomic, locked runtime writes.
- Blocking human approval with timeout, durable records, incident ID, canonical fix hash, and
  one-time approval consumption.
- Flask fallback UI and an AG-UI bridge used by the CopilotKit React page.
- Optional Auth0 access-token validation and reviewer attribution.
- Optional one-attempt, redacted Ambiguous document export.

## Install and verify

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev-c.txt
pytest -q
cd frontend
npm install
npm run build
```

## Run the local approval demo

Use three terminals from the repository root.

```bash
# terminal 1
source .venv/bin/activate
python -m memory_approval.approval_server
```

```bash
# terminal 2: creates a proposal and blocks for the decision
source .venv/bin/activate
python -m memory_approval.demo --external-server
```

```bash
# terminal 3
cd frontend
npm run dev
```

Open `http://127.0.0.1:5173`, load pending approvals, and approve or reject. The plain Flask
fallback is at `http://127.0.0.1:5050`.

## Environment

Copy `.env.dev-c.example` to `.env` and keep real credentials out of Git. Local mode needs no
vendor credentials. Auth0 activates only when its three `VITE_AUTH0_*` values are present and
`APPROVAL_AUTH_MODE=auth0`; the backend separately requires `AUTH0_DOMAIN` and
`AUTH0_AUDIENCE`. Ambiguous export stays disabled without `AMBIGUOUS_API_KEY`.

## Dev B integration handoff

The three public signatures in `CONTEXT.md` are unchanged. For exact mutation binding, the fixed
orchestrator must carry one incident ID through approval and application:

```python
from memory_approval.approvals import approval_context, consume_approval

with approval_context(incident_id):
    decision = request_approval(diagnosis, proposed_fix, confidence)

if not decision["approved"]:
    return needs_human
if not consume_approval(incident_id, proposed_fix):
    return needs_human
result = apply_fix(proposed_fix)
```

Call `consume_approval` immediately before `apply_fix`. Do not retry an uncertain mutation.
After a terminal outcome is known, the orchestrator may call `report_export`; export failure is
nonfatal and must not alter the pipeline outcome.
