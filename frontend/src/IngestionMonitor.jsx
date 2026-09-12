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

  return (
    <div className="stack">
      <div className="stats">
        <div className="stat">
          <div className="stat-label">Batches received</div>
          <div className="stat-value">{total}</div>
          <div className="stat-note">Latest 30 shown below</div>
        </div>
        <div className={`stat ${flagged ? "is-warn" : "is-ok"}`}>
          <div className="stat-label">Flagged</div>
          <div className="stat-value">{flagged}</div>
          <div className="stat-note">of {batches.length} recent samples</div>
        </div>
        <div className="stat">
          <div className="stat-label">Stream</div>
          <div className="stat-value" style={{ fontSize: 19, display: "flex", alignItems: "center", gap: 9 }}>
            <span className={`live-dot ${connected ? "on" : "off"}`} />
            {connected ? "Connected" : "Connecting"}
          </div>
          <div className="stat-note">WebSocket live feed</div>
        </div>
        <div className="stat">
          <div className="stat-label">Transmitter</div>
          <div className="stat-value" style={{ fontSize: 19 }}>{replaying ? "Running" : "Idle"}</div>
          <div className="stat-note">{transmitter.sent || 0} samples sent this session</div>
        </div>
      </div>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Iris transmission API</h2>
            <p>
              The backend sends one random Kaggle Iris sample every two seconds. Each sample has a
              30% chance of one field changing type. Validation runs on arrival — no model involved.
            </p>
          </div>
          <button className="btn secondary" disabled={busy} onClick={controlTransmitter}>
            {replaying ? "Stop transmitter" : "Start transmitter"}
          </button>
        </div>
        {transmitter.error && <div className="alert" style={{ marginTop: 10 }}>{transmitter.error}</div>}
        <small>
          Continues when you close this page. Stops on request, server shutdown, or the 1,000-batch
          storage limit. Clean source rows remain unchanged.
        </small>
      </section>

      {error && <div className="alert" role="alert">{error}</div>}

      {!batches.length ? (
        <div className="empty">
          <h3>Waiting for incoming rows</h3>
          <p>Start the transmitter above, or send your pipeline’s batches to <code>POST /api/ingestion</code>.</p>
        </div>
      ) : (
        <section className="feed">
          <div className="feed-head">
            <h2 style={{ fontSize: 15 }}>Recent samples</h2>
            <span className="pill">{flagged} flagged / {batches.length} recent</span>
          </div>

          <div className="feed-list" role="list" aria-label="Incoming samples">
            {batches.map(batch => (
              <div role="listitem" key={batch.batch_id}>
                <button className={`sample-row ${batch.flagged_count ? 'drift-row' : ''}`}
                        aria-pressed={selectedId === batch.batch_id}
                        onClick={() => setSelectedId(selectedId === batch.batch_id ? null : batch.batch_id)}>
                  <time>{new Date(batch.created_at).toLocaleTimeString()}</time>
                  <span className="sample-name">
                    Sample {batch.replay?.original?.Id ?? batch.batch_id.slice(0, 8)}
                  </span>
                  {batch.flagged_count
                    ? <span className="pill warn">{batch.mismatches[0]?.column}: type mismatch</span>
                    : <span className="pill ok">Valid</span>}
                </button>
              </div>
            ))}
          </div>

          {!selected ? (
            <div className="detail-panel">
              <p className="muted" style={{ fontSize: 13 }}>
                Select a sample to inspect its values. Detection runs in the backend even when this
                page is closed.
              </p>
            </div>
          ) : (
            <div className="detail-panel">
              <div className="card-head">
                <h3>{selected.flagged_count ? 'Flagged sample' : 'Valid sample'}</h3>
                {selected.flagged_count
                  ? <span className="pill warn">{selected.flagged_count} mismatch{selected.flagged_count > 1 ? 'es' : ''}</span>
                  : <span className="pill ok">Schema valid</span>}
              </div>

              <div className="detail-grid">
                {selected.replay && (
                  <div>
                    <div className="code-label" style={{ marginTop: 0 }}>Original row</div>
                    <pre>{JSON.stringify(selected.replay.original, null, 2)}</pre>
                  </div>
                )}
                {selected.flagged_count > 0 && (
                  <div>
                    <div className="code-label" style={{ marginTop: 0 }}>Detected mismatches</div>
                    <pre>{JSON.stringify(selected.mismatches, null, 2)}</pre>
                  </div>
                )}
              </div>

              {selected.flagged_count > 0 && (
                <>
                  <details>
                    <summary>Received sample</summary>
                    <pre>{JSON.stringify(selected.flagged_samples, null, 2)}</pre>
                  </details>
                  <div className="actions">
                    <button className="btn" disabled={busy} onClick={() => investigate(selected)}>
                      {selected.run_id ? 'Open investigation' : 'Investigate sample'}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
