import React, { useEffect, useState } from "react";

export default function IngestionMonitor({ request = fetch, onInvestigate }) {
  const [batches, setBatches] = useState([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [replaying, setReplaying] = useState(false);
  const [drift, setDrift] = useState(false);
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    let stopped = false, timer;
    const controller = new AbortController();
    async function poll() {
      try {
        const response = await request('/pipeline-api/ingestion', { signal: controller.signal });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Cannot read ingestion stream');
        if (!stopped) { setBatches(data.batches); setTotal(data.total_batches); setConnected(true); setError(''); }
      } catch (e) { if (!stopped) { setConnected(false); setError(e.message); } }
      if (!stopped) timer = setTimeout(poll, 1000);
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); controller.abort(); };
  }, [request]);
  useEffect(() => {
    if (!replaying) return;
    let stopped = false, timer;
    async function emit() {
      try {
        const response = await request('/pipeline-api/ingestion/replay', { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ corrupt: drift }) });
        if (!response.ok) throw new Error('Replay ingestion failed; stopped to avoid retrying a batch');
      } catch (e) { if (!stopped) { setError(e.message); setReplaying(false); } return; }
      if (!stopped) timer = setTimeout(emit, 2000);
    }
    emit();
    return () => { stopped = true; clearTimeout(timer); };
  }, [replaying, drift, request]);
  async function investigate(batch) {
    setBusy(true);
    try {
      const response = await request(`/pipeline-api/ingestion/${batch.batch_id}/investigate`, { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Investigation could not start');
      onInvestigate(data.incident_id);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  return <section>
    <header><span className="eyebrow">INGESTION MONITOR · {connected ? 'CONNECTED' : 'CONNECTING'}</span>
      <h1>Watch the rows arrive.</h1>
      <p>Every incoming batch is checked against the expected schema on arrival. Mismatched samples are flagged immediately, without waiting for a model.</p>
      <p><strong>{total} batches received</strong> · Latest 30 shown · Dashboard refreshes every second</p>
    </header>
    <article><h2>Kaggle Iris replay</h2><p>No production source is connected yet. Every two seconds, the backend selects one random flower sample from 150 clean Kaggle Iris rows stored in SQLite.</p>
      <div className="actions"><button onClick={() => setReplaying(!replaying)}>{replaying ? 'Stop replay' : 'Start replay'}</button>
        <button className="reject" aria-pressed={drift} onClick={() => setDrift(!drift)}>{drift ? 'Send clean samples' : 'Corrupt a random numeric field'}</button></div>
      <small>{drift ? 'Each new sample has one randomly selected measurement changed from number to string.' : 'New samples keep their original types.'} Replay stops when this page closes; the backend keeps accepting ingestion.</small>
    </article>
    {error && <p className="error" role="alert">{error}</p>}
    {!batches.length && <p>Waiting for incoming rows. Start the replay or send your pipeline’s batches to POST /api/ingestion.</p>}
    {batches.map(batch => <article key={batch.batch_id}>
      <span className="eyebrow">{batch.status === 'valid' ? 'SCHEMA VALID' : 'SCHEMA DRIFT DETECTED'}</span>
      <h2>{batch.row_count} rows received · {batch.flagged_count} flagged</h2>
      <small>{batch.dataset || "orders"} · {new Date(batch.created_at).toLocaleTimeString()} · {batch.batch_id}</small>
      {batch.replay && <details><summary>Original Kaggle row{batch.replay.changed_column ? ` · changed ${batch.replay.changed_column}` : ''}</summary><pre>{JSON.stringify(batch.replay.original, null, 2)}</pre></details>}
      {batch.flagged_count > 0 && <><pre>{JSON.stringify(batch.mismatches, null, 2)}</pre>
        <details><summary>Flagged input samples</summary><pre>{JSON.stringify(batch.flagged_samples, null, 2)}</pre></details>
        <button disabled={busy} onClick={() => investigate(batch)}>{batch.run_id ? 'Open investigation' : 'Investigate flagged batch'}</button>
        <small>Investigation sends this batch’s evidence to your configured model. Repairs still require approval.</small></>}
    </article>)}
  </section>;
}
