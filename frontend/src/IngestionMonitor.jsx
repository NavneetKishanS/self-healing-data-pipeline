import React, { useEffect, useState } from "react";

export default function IngestionMonitor({ request = fetch, onInvestigate }) {
  const [batches, setBatches] = useState([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [replaying, setReplaying] = useState(false);
  const [transmitter, setTransmitter] = useState({});
  const [busy, setBusy] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    let stopped = false, timer, socket;
    const controller = new AbortController();
    async function connect() {
      try {
        const response = await request('/pipeline-api/transmitter/ticket', { method: 'POST', signal: controller.signal });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Stream authentication failed');
        if (stopped) return;
        socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/pipeline-api/transmitter/stream`);
        socket.onopen = () => socket.send(data.ticket);
        socket.onmessage = event => {
          const snapshot = JSON.parse(event.data);
          setTransmitter(snapshot.transmitter || {}); setReplaying(Boolean(snapshot.transmitter?.running));
          setBatches(snapshot.batches); setTotal(snapshot.total_batches); setConnected(true); setError('');
        };
        socket.onclose = () => { if (!stopped) { setConnected(false); timer = setTimeout(connect, 2000); } };
        socket.onerror = () => { if (!stopped) setError('Live stream disconnected; reconnecting…'); };
      } catch (e) {
        if (!stopped) { setConnected(false); setError(e.message); timer = setTimeout(connect, 3000); }
      }
    }
    connect();
    return () => { stopped = true; clearTimeout(timer); controller.abort(); socket?.close(); };
  }, [request]);
  const selected = batches.find(batch => batch.batch_id === selectedId);
  const flagged = batches.filter(batch => batch.flagged_count > 0).length;
  async function controlTransmitter() {
    setBusy(true);
    try {
      const response = await request(`/pipeline-api/transmitter/${replaying ? 'stop' : 'start'}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interval_seconds: 2, corruption_probability: 0.3 }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Transmitter request failed');
      setTransmitter(data); setReplaying(Boolean(data.running));
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
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
      <p><strong>{total} batches received</strong> · Latest 30 shown · WebSocket live stream</p>
    </header>
    <article><h2>Iris transmission API</h2><p>The backend sends one random Kaggle Iris sample every two seconds. Each sample has a 30% chance of one random field changing type.</p>
      <button disabled={busy} onClick={controlTransmitter}>{replaying ? 'Stop transmitter' : 'Start transmitter'}</button>
      <p>{transmitter.sent || 0} samples sent in this transmitter session.</p>
      {transmitter.error && <p className="error">{transmitter.error}</p>}
      <small>Continues when you close this page. Stops on request, server shutdown, or the 1,000-batch storage limit. Clean source rows remain unchanged.</small>
    </article>
    {error && <p className="error" role="alert">{error}</p>}
    {!batches.length && <p>Waiting for incoming rows. Start the transmitter or send your pipeline’s batches to POST /api/ingestion.</p>}
    <article className="stream-panel">
      <div className="stream-heading"><h2>Recent samples</h2><span>{flagged} flagged / {batches.length} recent</span></div>
      <div className="sample-list" role="list" aria-label="Incoming samples">
        {batches.map(batch => <div role="listitem" key={batch.batch_id}>
          <button className={`sample-row ${batch.flagged_count ? 'drift-row' : ''}`} aria-pressed={selectedId === batch.batch_id}
            onClick={() => setSelectedId(selectedId === batch.batch_id ? null : batch.batch_id)}>
            <time>{new Date(batch.created_at).toLocaleTimeString()}</time>
            <span>Sample {batch.replay?.original?.Id ?? batch.batch_id.slice(0, 8)}</span>
            <strong>{batch.flagged_count ? `${batch.mismatches[0]?.column}: type mismatch` : 'Valid'}</strong>
          </button>
        </div>)}
      </div>
      {!selected && <p className="muted">Select a sample to inspect its values. Detection runs in the backend even when this page is closed.</p>}
      {selected && <div className="sample-detail"><h3>{selected.flagged_count ? 'Flagged sample' : 'Valid sample'}</h3>
        {selected.replay && <><h4>Original row</h4><pre>{JSON.stringify(selected.replay.original, null, 2)}</pre></>}
        {selected.flagged_count > 0 && <><h4>Detected mismatches</h4><pre>{JSON.stringify(selected.mismatches, null, 2)}</pre>
          <details><summary>Received sample</summary><pre>{JSON.stringify(selected.flagged_samples, null, 2)}</pre></details>
          <button disabled={busy} onClick={() => investigate(selected)}>{selected.run_id ? 'Open investigation' : 'Investigate sample'}</button></>}
      </div>}
    </article>
  </section>;
}
