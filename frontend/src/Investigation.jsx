import React, { useEffect, useRef, useState } from "react";

import IngestionMonitor from "./IngestionMonitor.jsx";

const storageKey = "pipeline-investigation";

export default function Investigation({ request = fetch }) {
  const [runId, setRunId] = useState(() => sessionStorage.getItem(storageKey) || "");
  const [run, setRun] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const guard = useRef(false);
  const result = run?.result;
  const pending = run?.approval;
  const finished = run?.status === "finished";

  async function api(path, options = {}) {
    const response = await request(`/pipeline-api${path}`, {
      ...options, headers: { "Content-Type": "application/json", ...options.headers },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Agent request failed (${response.status}). Check that the agent server is running.`);
    return data;
  }

  useEffect(() => {
    if (!runId || finished) return;
    let cancelled = false;
    let timer;
    const controller = new AbortController();
    async function poll() {
      try {
        const state = await api(`/runs/${runId}`, { signal: controller.signal });
        if (!cancelled) { setRun(state); setError(""); }
        if (state.status === "finished" || state.warning) return;
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) timer = setTimeout(poll, 1500);
    }
    poll();
    return () => { cancelled = true; clearTimeout(timer); controller.abort(); };
  }, [runId, finished, request]);

  function openIncident(id) {
    sessionStorage.setItem(storageKey, id);
    setRun(null); setRunId(id); setNote(""); setError("");
  }

  async function decide(approved) {
    if (guard.current || !pending) return;
    guard.current = true; setBusy(true); setError("");
    try {
      await api(`/runs/${runId}/decision`, { method: "POST", body: JSON.stringify({
        approved, fix_hash: pending.fix_hash, human_note: note,
      }) });
      setRun(previous => ({ ...previous, status: "running", approval: null }));
    } catch (e) { setError(e.message); }
    finally { guard.current = false; setBusy(false); }
  }

  const verified = result?.outcome === "fixed";

  return (
    <div className="stack">
      <IngestionMonitor request={request} onInvestigate={openIncident} />

      {error && <div className="alert" role="alert">{error}</div>}

      {runId && (
        <section className="card" aria-live="polite">
          <div className="card-head">
            <div>
              <span className="eyebrow">Incident status</span>
              <h2 style={{ marginTop: 6 }}>
                {finished
                  ? (verified ? "Repair verified" : "Stopped without a verified repair")
                  : pending ? "Your approval is needed" : "Investigating the batch…"}
              </h2>
            </div>
            <span className={`pill ${finished ? (verified ? "ok" : "warn") : pending ? "warn" : "info"}`}>
              {finished ? (verified ? "Verified" : "Needs a human") : pending ? "Blocking" : "Running"}
            </span>
          </div>

          {!finished && !pending && (
            <>
              <div className="working"><span className="spinner" /> Collecting evidence and checking the proposal…</div>
              <p className="muted" style={{ marginTop: 10, fontSize: 13 }}>
                No change is applied before approval.
              </p>
            </>
          )}

          {run?.warning && <div className="alert warn" style={{ marginTop: 12 }}>{run.warning}</div>}
          <small>Incident {runId}</small>
        </section>
      )}

      {pending && (
        <section className="card flagged">
          <div className="card-head">
            <div>
              <span className="eyebrow">Review before applying</span>
              <h2 style={{ marginTop: 6 }}>Proposed repair</h2>
            </div>
            <span className="pill warn">Blocking</span>
          </div>

          <p className="lede">{pending.diagnosis}</p>

          <div className="kv" style={{ marginTop: 18 }}>
            <div>
              <div className="kv-key">Model confidence</div>
              <div className="kv-val">{Math.round(pending.confidence * 100)}%</div>
            </div>
            <div>
              <div className="kv-key">Fix type</div>
              <div className="kv-val">{pending.proposed_fix?.fix_type}</div>
            </div>
            <div>
              <div className="kv-key">Target</div>
              <div className="kv-val">{pending.proposed_fix?.target}</div>
            </div>
          </div>

          <div className="code-label">Exact change to be applied</div>
          <pre>{JSON.stringify(pending.proposed_fix, null, 2)}</pre>

          <label htmlFor="decision-note">Decision note (optional)</label>
          <textarea id="decision-note" maxLength={1000} value={note}
                    placeholder="Context for the audit trail."
                    onChange={e => setNote(e.target.value)} />

          <div className="actions">
            <button className="btn" disabled={busy} onClick={() => decide(true)}>Approve conversion</button>
            <button className="btn danger" disabled={busy} onClick={() => decide(false)}>Reject repair</button>
          </div>
          <small>Approval expires if left unanswered. Rejection leaves the batch unchanged.</small>
        </section>
      )}

      {result && (
        <section className="card">
          <div className="card-head">
            <div>
              <span className="eyebrow">Run result</span>
              <h2 style={{ marginTop: 6 }}>{result.reason}</h2>
            </div>
            <span className={`pill ${verified ? "ok" : "warn"}`}>{result.outcome}</span>
          </div>

          {result.diagnosis && <p className="lede">{result.diagnosis.diagnosis}</p>}

          <div className="kv" style={{ marginTop: 18 }}>
            <div>
              <div className="kv-key">Mutation</div>
              <div className="kv-val" style={{ textTransform: "capitalize" }}>{result.mutation_state || "unknown"}</div>
            </div>
            <div>
              <div className="kv-key">Model calls</div>
              <div className="kv-val">{result.model_calls ?? "—"}</div>
            </div>
            {result.verification && (
              <>
                <div>
                  <div className="kv-key">Rows validated</div>
                  <div className="kv-val">{result.verification.row_count}</div>
                </div>
                <div>
                  <div className="kv-key">Schema</div>
                  <div className="kv-val">{result.verification.schema_valid ? "Valid" : "Not validated"}</div>
                </div>
              </>
            )}
          </div>

          {result.verification && (
            <details>
              <summary>Validated output sample</summary>
              <pre>{JSON.stringify(result.verification.sample_rows, null, 2)}</pre>
            </details>
          )}
          <details>
            <summary>Step history and full result</summary>
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </details>
        </section>
      )}
    </div>
  );
}
