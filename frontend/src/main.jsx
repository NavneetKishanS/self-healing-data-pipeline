import React, { useCallback, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { HttpAgent } from "@ag-ui/client";
import { Auth0Provider, useAuth0 } from "@auth0/auth0-react";
import { CopilotKit, useAgent, useCopilotKit } from "@copilotkit/react-core/v2";
import "./style.css";
import Investigation from "./Investigation.jsx";

const authConfig = {
  domain: import.meta.env.VITE_AUTH0_DOMAIN,
  clientId: import.meta.env.VITE_AUTH0_CLIENT_ID,
  audience: import.meta.env.VITE_AUTH0_AUDIENCE,
};
const configuredAuthValues = Object.values(authConfig).filter(Boolean).length;
const authEnabled = configuredAuthValues === 3;

function Review({ identityControls = null, apiFetch }) {
  const { agent } = useAgent({ agentId: "approval" });
  const { copilotkit } = useCopilotKit();
  const [started, setStarted] = useState(false);
  const [starting, setStarting] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  async function start() {
    if (starting) return;
    setStarting(true); setError("");
    try {
      // forwardedProps reach agent/server.py's POST /agent -> start(job_id, inject_failure).
      // job_1 + schema_drift is the only scenario the live adapter supports (see CONTEXT.md).
      await copilotkit.runAgent({ agent, forwardedProps: { job_id: "job_1", inject_failure: "schema_drift" } });
      setStarted(true);
    } catch (e) {
      setError("The server could not accept this request. It may already have an incident running.");
    } finally { setStarting(false); }
  }

  // Decisions go straight to the REST endpoint (agent/server.py POST /api/runs/<id>/decision),
  // not through copilotkit.runAgent - that call only ever starts/observes a run, it never reads
  // a "decision" forwardedProp. The SSE stream from start() above stays open and will push the
  // finished state once this resolves the backend's pending approval wait.
  async function decide(approved) {
    if (deciding || !pending) return;
    setDeciding(true); setError("");
    try {
      const response = await apiFetch(`/api/runs/${agent.state.incident_id}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved, human_note: note, fix_hash: pending.fix_hash }),
      });
      if (!response.ok) throw new Error("rejected");
    } catch (e) {
      setError("The decision could not be recorded. The proposal may have expired or already been decided.");
    } finally { setDeciding(false); }
  }

  const status = agent.state?.status;
  const pending = status === "awaiting_approval" ? agent.state?.approval : null;
  const result = agent.state?.result;

  return <main>
    <header>{identityControls}<span className="eyebrow">PIPELINE INCIDENT REVIEW</span><h1>Review a proposed repair</h1>
      <p>Inspect the diagnosis and exact change before allowing the pipeline to continue.</p>
      {!started && <button disabled={starting} onClick={start}>{starting ? "Starting investigation…" : "Start investigation (schema_drift)"}</button>}
    </header>
    {error && <p role="alert" className="error">{error}</p>}
    {started && !pending && !result && <article><h2>Investigating…</h2><p>Gathering evidence and reasoning about the failure. This can take up to a minute on some models.</p></article>}
    {pending && <article>
      <span className="eyebrow">AWAITING YOUR DECISION</span><h2>{pending.diagnosis}</h2>
      <p>Model confidence: {Math.round(pending.confidence * 100)}% <span className="muted">(model estimate)</span></p>
      <h3>Proposed change</h3><pre>{JSON.stringify(pending.proposed_fix, null, 2)}</pre>
      <label htmlFor="note">Decision note (optional)</label>
      <textarea id="note" maxLength={2000} value={note} onChange={e => setNote(e.target.value)} />
      <div className="actions"><button disabled={deciding} onClick={() => decide(true)}>Approve repair</button>
        <button className="reject" disabled={deciding} onClick={() => decide(false)}>Reject repair</button></div>
      <small>Incident: {pending.incident_id}</small>
    </article>}
    {result && <article>
      <span className="eyebrow">OUTCOME</span><h2>{result.outcome}</h2>
      <p>{result.reason}</p>
      {result.verification && <><h3>Rerun result</h3><pre>{JSON.stringify(result.verification, null, 2)}</pre></>}
    </article>}
    <footer>Local approval interface · CopilotKit / AG-UI · No repair runs in the browser</footer>
  </main>;
}

function ApprovalSurface({ agent, identityControls, request = fetch }) {
  const [view, setView] = useState("investigation");
  return <><nav className="view-nav" aria-label="Workspace">
    <button className="quiet" aria-pressed={view === "investigation"} onClick={() => setView("investigation")}>Dataset investigation</button>
    <button className="quiet" aria-pressed={view === "approvals"} onClick={() => setView("approvals")}>Approval inbox</button>
  </nav>{view === "investigation" ? <Investigation request={request} identityControls={identityControls} /> :
    <CopilotKit agents__unsafe_dev_only={{ approval: agent }}><Review identityControls={identityControls} apiFetch={request} /></CopilotKit>}</>;
}

function LocalApp() {
  const agent = useMemo(() => new HttpAgent({ url: "/ag-ui", agentId: "approval" }), []);
  return <ApprovalSurface agent={agent} request={fetch} />;
}

function AuthenticatedApp() {
  const { isLoading, isAuthenticated, user, loginWithRedirect, logout, getAccessTokenSilently } = useAuth0();
  const authorizedFetch = useMemo(() => async (url, init = {}) => {
    const token = await getAccessTokenSilently({ authorizationParams: {
      audience: authConfig.audience,
      scope: "read:incidents approve:fixes",
    }});
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    return fetch(url, { ...init, headers });
  }, [getAccessTokenSilently]);
  const agent = useMemo(() => new HttpAgent({ url: "/ag-ui", agentId: "approval", fetch: authorizedFetch }), [authorizedFetch]);

  const agentRequest = useCallback(async (url, init = {}) => {
    const token = await getAccessTokenSilently({ authorizationParams: {
      audience: authConfig.audience, scope: "read:incidents run:incidents approve:fixes",
    }});
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    return fetch(url, { ...init, headers });
  }, [getAccessTokenSilently]);

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
  return <ApprovalSurface agent={agent} identityControls={identityControls} request={agentRequest} />;
}

function Root() {
  if (configuredAuthValues > 0 && !authEnabled) return <main><article className="auth-card error">
    <h1>Auth0 configuration is incomplete</h1>
    <p>Set VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID and VITE_AUTH0_AUDIENCE together, or remove all three for local mode.</p>
  </article></main>;
  if (!authEnabled) return <LocalApp />;
  return <Auth0Provider domain={authConfig.domain} clientId={authConfig.clientId}
    authorizationParams={{ redirect_uri: window.location.origin, audience: authConfig.audience,
      scope: "openid profile email read:incidents run:incidents approve:fixes" }}>
    <AuthenticatedApp />
  </Auth0Provider>;
}

createRoot(document.getElementById("root")).render(<Root />);
