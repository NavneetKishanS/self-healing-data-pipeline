import React, { useCallback, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { HttpAgent } from "@ag-ui/client";
import { Auth0Provider, useAuth0 } from "@auth0/auth0-react";
import { CopilotKit, useAgent, useCopilotKit } from "@copilotkit/react-core/v2";
import "./style.css";
import Investigation from "./Investigation.jsx";
import HowItWorks from "./HowItWorks.jsx";

const authConfig = {
  domain: import.meta.env.VITE_AUTH0_DOMAIN,
  clientId: import.meta.env.VITE_AUTH0_CLIENT_ID,
  audience: import.meta.env.VITE_AUTH0_AUDIENCE,
};
const configuredAuthValues = Object.values(authConfig).filter(Boolean).length;
const authEnabled = configuredAuthValues === 3;

const VIEWS = {
  investigation: { label: "Ingestion monitor", title: "Ingestion monitor", blurb: "Live schema validation on every arriving batch." },
  approvals:     { label: "Approval inbox",   title: "Approval inbox",   blurb: "Review and authorize a proposed repair before it is applied." },
  how:           { label: "How it works",     title: "How it works",     blurb: "Architecture, guarantees, and what is real in this demo." },
};

function BrandMark({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M3 14.5l4.2-5.6 3.6 4.2 2.6-3.1L21 17" stroke="#fff" strokeWidth="2.1"
            strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="10.8" cy="13.1" r="1.9" fill="#0a5d50" stroke="#fff" strokeWidth="1.6" />
    </svg>
  );
}

/* ---------------- Approval inbox ---------------- */

function Review({ apiFetch }) {
  const { agent } = useAgent({ agentId: "approval" });
  const { copilotkit } = useCopilotKit();
  const [started, setStarted] = useState(false);
  const [starting, setStarting] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  const status = agent.state?.status;
  const pending = status === "awaiting_approval" ? agent.state?.approval : null;
  const result = agent.state?.result;

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
  // not through copilotkit.runAgent - that call only starts/observes a run, it never reads a
  // "decision" forwardedProp. The SSE stream from start() stays open and pushes the finished state.
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

  const fixed = result?.outcome === "fixed";
  // runAgent only resolves when the whole run ends, so `started` alone would leave the
  // intro card on screen through the entire incident. Any live agent state hides it.
  const active = starting || started || status || pending || result;

  return (
    <div className="stack">
      {!active && (
        <section className="card accent">
          <span className="eyebrow">Simulated incident</span>
          <h2 style={{ marginTop: 8 }}>Run a schema drift incident</h2>
          <p className="lede" style={{ marginTop: 8 }}>
            Injects a known <code>schema_drift</code> failure into <code>job_1</code>, gathers real
            evidence, and asks a model to diagnose and then critique its own proposal. Nothing is
            modified until you approve the exact change.
          </p>
          <div className="actions">
            <button className="btn" disabled={starting} onClick={start}>
              {starting ? "Starting…" : "Start investigation"}
            </button>
          </div>
        </section>
      )}

      {error && <div className="alert" role="alert">{error}</div>}

      {active && !pending && !result && (
        <section className="card">
          <div className="working"><span className="spinner" /> Investigating incident…</div>
          <p className="muted" style={{ marginTop: 10, fontSize: 13 }}>
            Reading logs and schema, searching past incidents, then two model passes — diagnose and
            critique. This can take up to a minute depending on the model.
          </p>
          <div className="steps-inline">
            <span className="step-chip">get_recent_logs</span>
            <span className="step-chip">get_schema</span>
            <span className="step-chip">search_past_incidents</span>
            <span className="step-chip">diagnose</span>
            <span className="step-chip">critique</span>
          </div>
        </section>
      )}

      {pending && (
        <section className="card flagged">
          <div className="card-head">
            <div>
              <span className="eyebrow">Awaiting your decision</span>
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

          <label htmlFor="note">Decision note (optional)</label>
          <textarea id="note" maxLength={2000} value={note}
                    placeholder="Context for the audit trail — why you approved or rejected this."
                    onChange={e => setNote(e.target.value)} />

          <div className="actions">
            <button className="btn" disabled={deciding} onClick={() => decide(true)}>Approve repair</button>
            <button className="btn danger" disabled={deciding} onClick={() => decide(false)}>Reject repair</button>
          </div>
          <small>
            Approval is bound to incident <code>{pending.incident_id}</code> and a hash of the fix
            above, so the payload you approve is the payload applied. Rejection stops the run.
          </small>
        </section>
      )}

      {result && (
        <section className="card">
          <div className="card-head">
            <div>
              <span className="eyebrow">Outcome</span>
              <h2 style={{ marginTop: 6, textTransform: "capitalize" }}>{String(result.outcome || "").replace("_", " ")}</h2>
              <p style={{ marginTop: 6 }}>{result.reason}</p>
            </div>
            <span className={`pill ${fixed ? "ok" : "warn"}`}>{fixed ? "Verified" : "Needs a human"}</span>
          </div>

          {result.verification && (
            <>
              <div className="kv" style={{ marginTop: 6 }}>
                <div>
                  <div className="kv-key">Rows</div>
                  <div className="kv-val">{result.verification.row_count} / {result.verification.expected_row_count}</div>
                </div>
                <div>
                  <div className="kv-key">Mismatches</div>
                  <div className="kv-val">{result.verification.mismatch_count ?? 0}</div>
                </div>
                <div>
                  <div className="kv-key">Schema</div>
                  <div className="kv-val">{result.verification.schema_valid ? "Valid" : "Not validated"}</div>
                </div>
                <div>
                  <div className="kv-key">Mutation</div>
                  <div className="kv-val" style={{ textTransform: "capitalize" }}>{result.mutation_state || "—"}</div>
                </div>
              </div>
              <details>
                <summary>Rerun result</summary>
                <pre>{JSON.stringify(result.verification, null, 2)}</pre>
              </details>
            </>
          )}
        </section>
      )}
    </div>
  );
}

/* ---------------- Shell ---------------- */

function ApprovalSurface({ agent, identityControls, request = fetch }) {
  const [view, setView] = useState("investigation");
  const meta = VIEWS[view];

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><BrandMark /></span>
          <span>
            <span className="brand-name">Mend</span>
            <span className="brand-sub">Pipeline reliability</span>
          </span>
        </div>

        <nav className="nav" aria-label="Workspace">
          <span className="nav-label">Workspace</span>
          {Object.entries(VIEWS).map(([key, item]) => (
            <button key={key} className="nav-item" aria-current={view === key ? "page" : undefined}
                    onClick={() => setView(key)}>
              <span className="nav-dot" />{item.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="env-row"><span className="live-dot on" /> Agent connected</div>
          <div className="env-row">{authEnabled ? "Auth0 protected" : "Local development mode"}</div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div>
            <h1>{meta.title}</h1>
            <p>{meta.blurb}</p>
          </div>
          {identityControls}
        </header>

        <main className="content">
          {view === "investigation" && <Investigation request={request} />}
          {view === "approvals" && (
            <CopilotKit agents__unsafe_dev_only={{ approval: agent }}>
              <Review apiFetch={request} />
            </CopilotKit>
          )}
          {view === "how" && <HowItWorks />}
        </main>
      </div>
    </div>
  );
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

  if (isLoading) return <div className="auth-shell"><article className="auth-card"><h1>Checking access…</h1></article></div>;
  if (!isAuthenticated) return (
    <div className="auth-shell">
      <article className="auth-card">
        <span className="brand-mark"><BrandMark size={24} /></span>
        <span className="eyebrow">Protected review</span>
        <h1 style={{ fontSize: 24, marginTop: 8 }}>Sign in to review repairs</h1>
        <p>An authenticated reviewer is required before a pipeline change can be approved.</p>
        <button className="btn" onClick={() => loginWithRedirect()}>Continue with Auth0</button>
      </article>
    </div>
  );

  const identityControls = (
    <div className="identity">
      <span>{user?.email || user?.name || "reviewer"}</span>
      <button className="btn secondary sm"
              onClick={() => logout({ logoutParams: { returnTo: window.location.origin } })}>Sign out</button>
    </div>
  );
  return <ApprovalSurface agent={agent} identityControls={identityControls} request={agentRequest} />;
}

function Root() {
  if (configuredAuthValues > 0 && !authEnabled) return (
    <div className="auth-shell">
      <article className="auth-card">
        <h1 style={{ fontSize: 22 }}>Auth0 configuration is incomplete</h1>
        <p>Set VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID and VITE_AUTH0_AUDIENCE together, or remove all three for local mode.</p>
      </article>
    </div>
  );
  if (!authEnabled) return <LocalApp />;
  return <Auth0Provider domain={authConfig.domain} clientId={authConfig.clientId}
    authorizationParams={{ redirect_uri: window.location.origin, audience: authConfig.audience,
      scope: "openid profile email read:incidents run:incidents approve:fixes" }}>
    <AuthenticatedApp />
  </Auth0Provider>;
}

createRoot(document.getElementById("root")).render(<Root />);
