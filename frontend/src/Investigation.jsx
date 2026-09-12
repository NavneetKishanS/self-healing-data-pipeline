import React, { useEffect, useRef, useState } from "react";

import IngestionMonitor from "./IngestionMonitor.jsx";

const storageKey = "pipeline-investigation";
export default function Investigation({ request = fetch, identityControls }) {
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

  return <main className="investigation">
    {identityControls}
    <IngestionMonitor request={request} onInvestigate={openIncident} />
    {error && <p role="alert" className="error">{error}</p>}
    {runId && <article aria-live="polite"><span className="eyebrow">INCIDENT STATUS</span>
      <h2>{finished ? (result?.outcome === "fixed" ? "Repair verified" : "Stopped without verified repair") : pending ? "Your approval is needed" : "Investigating the batch…"}</h2>
      {!finished && !pending && <p>Collecting dataset evidence, proposing a conversion, and checking the proposal. No change is applied before approval.</p>}
      {run?.warning && <p className="error">{run.warning}</p>}
      <small>Incident: {runId}</small>
    </article>}
    {pending && <article><span className="eyebrow">REVIEW BEFORE APPLYING</span><h2>Proposed repair</h2>
      <p>{pending.diagnosis}</p><pre>{JSON.stringify(pending.proposed_fix, null, 2)}</pre>
      <p>Model confidence: {Math.round(pending.confidence * 100)}% (estimate)</p>
      <label htmlFor="decision-note">Decision note (optional)</label>
      <textarea id="decision-note" maxLength={1000} value={note} onChange={e => setNote(e.target.value)} />
      <div className="actions"><button disabled={busy} onClick={() => decide(true)}>Approve conversion</button>
        <button className="reject" disabled={busy} onClick={() => decide(false)}>Reject repair</button></div>
      <small>Approval expires if left unanswered. Rejection leaves the batch unchanged.</small>
    </article>}
    {result && <article><span className="eyebrow">RUN RESULT</span><h2>{result.reason}</h2>
      {result.diagnosis && <p>{result.diagnosis.diagnosis}</p>}
      <p>Mutation: {result.mutation_state || "unknown"} · Model calls: {result.model_calls ?? "—"}</p>
      {result.verification && <><h3>Validated output</h3><p>{result.verification.row_count} rows · Schema {result.verification.schema_valid ? "valid" : "not validated"}</p>
        <pre>{JSON.stringify(result.verification.sample_rows, null, 2)}</pre></>}
      <details><summary>Step history and full result</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
    </article>}
    <footer>Local dataset demonstration · Actual row conversion · Human approval required</footer>
  </main>;
}
