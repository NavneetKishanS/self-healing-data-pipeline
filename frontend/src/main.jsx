import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { HttpAgent } from "@ag-ui/client";
import { Auth0Provider, useAuth0 } from "@auth0/auth0-react";
import { CopilotKit, useAgent, useCopilotKit } from "@copilotkit/react-core/v2";
import "./style.css";

const authConfig = {
  domain: import.meta.env.VITE_AUTH0_DOMAIN,
  clientId: import.meta.env.VITE_AUTH0_CLIENT_ID,
  audience: import.meta.env.VITE_AUTH0_AUDIENCE,
};
const configuredAuthValues = Object.values(authConfig).filter(Boolean).length;
const authEnabled = configuredAuthValues === 3;

function Review({ identityControls = null }) {
  const { agent } = useAgent({ agentId: "approval" });
  const { copilotkit } = useCopilotKit();
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notes, setNotes] = useState({});

  async function run(decision) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      await copilotkit.runAgent({ agent, forwardedProps: decision ? { decision } : {} });
      setLoaded(true);
    } catch (e) {
      setError("The server could not accept this request. Refresh approvals; the proposal may have expired or already been decided.");
    } finally { setBusy(false); }
  }
  function decide(record, decision) {
    const { approval_id, incident_id, fix_hash, decision_token } = record;
    return run({ approval_id, incident_id, fix_hash, decision_token, decision, note: notes[approval_id] || "" });
  }
  const records = agent.state?.approvals || [];
  return <main>
    <header>{identityControls}<span className="eyebrow">PIPELINE INCIDENT REVIEW</span><h1>Review a proposed repair</h1>
      <p>Inspect the diagnosis and exact change before allowing the pipeline to continue.</p>
      <button disabled={busy} onClick={() => run()}>{busy ? "Contacting backend…" : "Load pending approvals"}</button>
    </header>
    {error && <p role="alert" className="error">{error}</p>}
    {agent.state?.result?.recorded && <p role="status" className="success">Your {agent.state.result.decision === "approve" ? "approval" : "rejection"} was recorded. The pipeline worker receives this decision separately.</p>}
    {loaded && records.length === 0 && <article><h2>No pending approvals</h2><p>Start an investigation or the local approval demo, then load approvals again.</p></article>}
    {records.map(record => <article key={record.approval_id}>
      <span className="eyebrow">AWAITING YOUR DECISION</span><h2>{record.diagnosis}</h2>
      <p>Model confidence: {Math.round(record.confidence * 100)}% <span className="muted">(model estimate)</span></p>
      <h3>Proposed change</h3><pre>{JSON.stringify(record.proposed_fix, null, 2)}</pre>
      <label htmlFor={`note-${record.approval_id}`}>Decision note (optional)</label>
      <textarea id={`note-${record.approval_id}`} maxLength={2000} value={notes[record.approval_id] || ""}
        onChange={e => setNotes({ ...notes, [record.approval_id]: e.target.value })} />
      <div className="actions"><button disabled={busy} onClick={() => decide(record, "approve")}>Approve repair</button>
        <button className="reject" disabled={busy} onClick={() => decide(record, "reject")}>Reject repair</button></div>
      <small>Incident: {record.incident_id}<br/>Expires: {new Date(record.expires_at * 1000).toLocaleTimeString()}</small>
    </article>)}
    <footer>Local approval interface · CopilotKit / AG-UI · No repair runs in the browser</footer>
  </main>;
}

function ApprovalSurface({ agent, identityControls }) {
  return <CopilotKit agents__unsafe_dev_only={{ approval: agent }}>
    <Review identityControls={identityControls} />
  </CopilotKit>;
}

function LocalApp() {
  const agent = useMemo(() => new HttpAgent({ url: "/ag-ui", agentId: "approval" }), []);
  return <ApprovalSurface agent={agent} />;
}

function AuthenticatedApp() {
  const { isLoading, isAuthenticated, user, loginWithRedirect, logout, getAccessTokenSilently } = useAuth0();
  const agent = useMemo(() => new HttpAgent({
    url: "/ag-ui",
    agentId: "approval",
    fetch: async (url, init) => {
      const token = await getAccessTokenSilently({ authorizationParams: {
        audience: authConfig.audience,
        scope: "read:incidents approve:fixes",
      }});
      const headers = new Headers(init.headers);
      headers.set("Authorization", `Bearer ${token}`);
      return fetch(url, { ...init, headers });
    },
  }), [getAccessTokenSilently]);

  if (isLoading) return <main><article className="auth-card"><h1>Checking access…</h1></article></main>;
  if (!isAuthenticated) return <main><article className="auth-card">
    <span className="eyebrow">PROTECTED REVIEW</span>
    <h1>Sign in to review repairs</h1>
    <p>An authenticated reviewer is required before a pipeline change can be approved.</p>
    <button onClick={() => loginWithRedirect()}>Continue with Auth0</button>
  </article></main>;

  const identityControls = <div className="identity">
    <span>Signed in as {user?.email || user?.name || "reviewer"}</span>
    <button className="quiet" onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}>Sign out</button>
  </div>;
  return <ApprovalSurface agent={agent} identityControls={identityControls} />;
}

function Root() {
  if (configuredAuthValues > 0 && !authEnabled) return <main><article className="auth-card error">
    <h1>Auth0 configuration is incomplete</h1>
    <p>Set VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID and VITE_AUTH0_AUDIENCE together, or remove all three for local mode.</p>
  </article></main>;
  if (!authEnabled) return <LocalApp />;
  return <Auth0Provider domain={authConfig.domain} clientId={authConfig.clientId}
    authorizationParams={{ redirect_uri: window.location.origin, audience: authConfig.audience,
      scope: "openid profile email read:incidents approve:fixes" }}>
    <AuthenticatedApp />
  </Auth0Provider>;
}

createRoot(document.getElementById("root")).render(<Root />);
