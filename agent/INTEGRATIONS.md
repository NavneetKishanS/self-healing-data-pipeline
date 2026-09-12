# Live integration handoff

Dev B owns the files in `agent/`. This adds no frontend and does not change Dev A/C's files.

## What's connected

- Model: `model_client.LiveModel` calls LiteLLM with the selected `LLM_MODEL`, a real provider key,
  an explicit timeout/output cap, and zero SDK retries. Jinja produces both model requests.
- Dataset: `dataset.OrdersDataset` loads the first 30 rows of the team's orders CSV, injects string values,
  converts approved numeric strings, and validates the actual rows against an unchanged schema.
  Dev A's pipeline files are untouched. Exa is removed; no external research is performed.
- Ambiguous: `reporting.export_report` creates a document at
  `https://app.ambiguous.ai/api/documents` after termination. SQLite under ignored `agent/.state/`
  prevents automatic repeats of a sent or uncertain export. Known environment secrets are redacted;
  this demo contains synthetic data. Review/redact business data before adapting it to production.
  If Dev C adds `memory_approval.report_export.report_export(result)`, the live runner uses that
  function instead; export errors do not erase or replay the repair outcome.
- Auth0: `auth.Auth0Verifier` validates RS256 signatures via tenant JWKS, issuer, audience, expiry,
  and permissions. API ownership is tied to verified `sub`, never a browser-supplied user ID.
- CopilotKit: `server.py` exposes the real workflow over AG-UI; `copilotkit/runtime.mjs` registers
  that endpoint as a real `HttpAgent` in the CopilotKit runtime. It verifies the session through
  Python before exposing runtime state and isolates in-memory runtime instances per user.

## Configuration

Configure credentials in the repo-root `.env`.
The default LLM uses OpenRouter: set `OPENROUTER_API_KEY` and choose a chat model using
`LLM_MODEL=openrouter/<author>/<model>` (default: `openrouter/anthropic/claude-sonnet-4.6`).
Auth0's domain/client ID are public configuration, while vendor API keys remain server-side.
Dev C supplies the frontend's Auth0 application/client ID and configured login/logout callbacks.
Configure an Auth0 API with the chosen `AUTH0_AUDIENCE` and grant the appropriate permissions:
`read:incidents`, `run:incidents`, and `approve:fixes`. Request that audience during login so the
frontend receives an API access token, not just an ID token. The initial demo only allows `job_1`;
multi-workspace job access needs an explicit ACL before broadening it.

## Start the backend

From the repository root:

```sh
.venv/bin/python -B -m agent serve
```

This requires Auth0 configuration. For explicit loopback development without configured Auth0:

```sh
.venv/bin/python -B -m agent serve --local-no-auth
```

Both modes bind to `127.0.0.1:8000`. Do not deploy the no-auth mode. It rejects cross-origin browser
requests; use the same-origin runtime proxy below. It does not stand in for an Auth0 demo.

Start the separate runtime in a second terminal (Node 24 recommended):

```sh
cd agent/copilotkit
npm ci --ignore-scripts
npm start
```

The runtime listens on `127.0.0.1:4000/api/copilotkit`. `PIPELINE_AGENT_URL` can select a different
backend endpoint. Set `COPILOTKIT_TELEMETRY_DISABLED=true` if telemetry is not desired.

## Dev C frontend connection

Either import `createPipelineHandler` from `agent/copilotkit/runtime.mjs` into the frontend server,
or proxy `/api/copilotkit/*`, `/api/runs/*`, and `/api/integrations` to the standalone runtime.
Use CopilotKit's multi-route transport (`useSingleEndpoint={false}`) and agent ID `default`.
Forward the user's `Authorization: Bearer <access-token>` header on runtime and approval requests.
Do not replace it with a shared service token: ownership depends on the actual user identity.
No second LLM or CopilotKit built-in agent is needed.

The AG-UI agent endpoint is `POST /agent`. Send standard `RunAgentInput` with unique `runId`,
`threadId`, and `forwardedProps: {"job_id":"job_1", "inject_failure":"schema_drift"}` for the
synthetic demo. Omit `inject_failure` to investigate existing failure state. Arbitrary chat text,
client tool definitions, and client state are not treated as permission to mutate anything.

The stream emits `RUN_STARTED`, `STATE_SNAPSHOT`, then `RUN_FINISHED`. Its snapshot contains:

- `status`: `running`, `awaiting_approval`, or `finished`.
- `approval`: diagnosis, proposed_fix, confidence, fix_hash, incident_id while a decision is pending.
- `result`: the full terminal workflow result, model usage, integration status, limitations, and
  Ambiguous document link or explicit delivery error.

Render the approval card from this state. On a click, send:

```text
POST /api/runs/<incident_id>/decision
Authorization: Bearer <access-token>
Content-Type: application/json

{"approved":true,"fix_hash":"<hash from current approval>","human_note":"Reviewed"}
```

The Python backend verifies permission, user ownership, expiry, exact hash, and duplicate clicks.
Approval waits in a worker, leaving the web server responsive. The decision's verified subject is
included in the approval record. No browser refresh or duplicate run request repeats a write.

REST clients can alternatively start with `POST /api/runs` and
`{"runId":"unique-id","job_id":"job_1","inject_failure":"schema_drift"}`, then poll
`GET /api/runs/<id>`. Runs are serialized. After a process restart, the saved outcome is readable,
but pending work is not automatically resumed; used run IDs cannot start another repair.

## Verification and limits

```sh
.venv/bin/python -B -m unittest discover -s agent/tests -v
cd agent/copilotkit && npm test
```

The tests mock vendor HTTP responses and exercise the real AG-UI encoder, approval round trip,
JWT signature validation, export deduplication, and CopilotKit runtime discovery. They do not
prove external account permissions. Use real keys and a synthetic approved incident for the final
service rehearsal. The CLI and server share the same live runner; the old `agent.demo` remains an
explicitly scripted teaching example and is never called as a live fallback.
