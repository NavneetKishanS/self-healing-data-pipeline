# Dataset investigation UI

From the repository root, start the agent (restart an existing agent process after code changes):

```sh
AGENT_PORT=8001 .venv/bin/python -B -m agent serve --local-no-auth
```

In another terminal:

```sh
npm --prefix frontend run dev
```

Open http://127.0.0.1:5173. The ingestion dashboard refreshes every second.
Click **Start replay** for random Kaggle Iris samples every two seconds, then **Corrupt a random numeric field**
to convert one randomly selected measurement to a string. Validation runs automatically for every arriving batch;
it does not need a model. Stop replay when done. Browser replay stops on closing the page.

Your real ingestion producer must POST `{ "batchId": "unique-id", "rows": [...] }` to
`http://127.0.0.1:8001/api/ingestion`. JSON preserves numeric versus string types.
Each row uses order_id, customer_id, amount, created_at. Send 1–100 rows per batch.
Authenticated deployments require a bearer token with run:incidents; readers need read:incidents.
Repeated identical batch IDs are idempotent; a changed payload under the same ID is rejected.
Data and flags persist in the local agent SQLite database, capped at 1000 batches per owner.
This is an ingestion validation boundary; it is not yet connected to a production broker or sink.

Flagged rows show expected type, received type, and actual value. **Investigate flagged batch**
sends the stored batch to the agent; approval and results appear below the feed. The model still
must complete critique before approval. Repairs operate on an isolated copy, not the upstream
source; original drift flags remain as the immutable ingestion record. Refresh resumes the same
investigation. A restarted process never automatically replays pending repair work.

The investigation uses `/pipeline-api` through Vite's loopback proxy to the agent on
port 8001. Approval decisions include the pending proposal's hash. Existing **Approval
inbox** functionality still uses Dev C's service on port 5050 and CopilotKit/AG-UI.

For Auth0, configure all three VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID, and
VITE_AUTH0_AUDIENCE settings in root `.env`, run the agent without `--local-no-auth`,
and grant read:incidents, run:incidents, approve:fixes permissions. No model API keys
belong in VITE_ variables. Local mode needs no Auth0 configuration.

`npm --prefix frontend run build` checks the frontend production build. Production
hosting must supply equivalent authenticated reverse proxies; Vite's proxy is dev-only.

## Kaggle replay and storage

The bundled dataset is Kaggle `uciml/iris`, 150 rows, CC0. See `agent/fixtures/README.md`
for provenance and checksum. Startup seeds immutable typed originals into `iris_source`
in `agent/.state/runs.sqlite`. SQLite is sufficient for this local single-server demo.
The expected Iris schema is defined independently in `agent/iris.py`.

`POST /api/ingestion/replay` with `{"corrupt":true}` samples a random row and a random
numeric measurement, converts only that value to a string, validates it, and persists
both original and received versions with the flag. `false` emits a clean sample.
The UI calls this every two seconds while replay is enabled. It is a simulated source
using real Kaggle data, not a production data connector. Stored events survive restarts;
replay scheduling stops when the browser closes. No model is required for detection.
Iris investigations use the exact persisted sample and support approved numeric conversion.
External producers can specify `"dataset":"iris"` on `/api/ingestion`; omitted means orders.
