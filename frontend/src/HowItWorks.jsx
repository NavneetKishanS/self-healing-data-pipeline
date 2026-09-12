import React from "react";

const SEQUENCE = [
  { title: "Ingestion validation", tag: "no model", body: "Every arriving batch is checked against the expected schema the moment it lands. Type mismatches are flagged immediately — detection never waits on a model and never costs a token." },
  { title: "get_recent_logs", tag: "tool 1", body: "Reads the failing job's real status, error type, affected table, and row counts." },
  { title: "get_schema", tag: "tool 2", body: "Reads the current column types for the affected table, plus the repair contract describing what a valid fix may change." },
  { title: "search_past_incidents", tag: "tool 3", body: "Looks up prior incidents of the same error type — what was tried, what worked, and what was later reverted." },
  { title: "Diagnose", tag: "model call 1", cls: "model", body: "The model receives the gathered evidence and returns a structured diagnosis with exactly one proposed fix and a confidence score. It cannot call tools itself." },
  { title: "Critique own fix", tag: "model call 2", cls: "model", body: "A second pass argues against the first, using the same evidence. If it disagrees, the run stops at needs_human before anything is touched." },
  { title: "Procedural guard", tag: "policy", cls: "gate", body: "If the proposed fix type has been pruned for this error type — because a human rejected it or it failed verification before — Python halts the run here, before any approval is even requested." },
  { title: "request_approval", tag: "human", cls: "gate", body: "A person reviews the diagnosis and the exact change. Approval is bound to the incident ID and a hash of the proposed fix, so the payload approved is the payload applied." },
  { title: "apply_fix", tag: "tool 4", body: "Converts the affected values on an isolated copy. A refusal here stops the run; it never pretends a repair happened." },
  { title: "rerun_pipeline", tag: "tool 5", body: "Re-validates against the real current state. Row count and schema validity must both pass — a status flag alone is not accepted as proof." },
  { title: "log_incident", tag: "tool 6", body: "Writes the outcome back to incident memory so the next occurrence resolves faster." },
];

const GUARDS = [
  { title: "Bounded by construction", body: "Max 8 tool calls and 3 model calls per incident, counted in Python. The model cannot talk itself into another step." },
  { title: "Three terminal states", body: "Every path ends in exactly one of fixed, needs_human, or gave_up. No state loops back into itself." },
  { title: "No silent retries", body: "A rejected fix is never retried automatically. Malformed model output gets exactly one correction attempt, then stops." },
  { title: "Approval integrity", body: "The approved fix is hashed. If anything edits the payload between approval and apply, the run stops and asks for a fresh decision." },
  { title: "Fixed sequence, not agent discretion", body: "Python decides which tool runs next, in what order, every time. The model reasons; it does not drive execution." },
  { title: "Verified, not assumed", body: "Repairs run on an isolated copy and are checked against the expected schema. The original drift record is left intact." },
];

export default function HowItWorks() {
  return (
    <div className="stack">
      <section className="card accent">
        <span className="eyebrow">Overview</span>
        <h2 style={{ fontSize: 22, marginTop: 8 }}>One agent, many tools, a human in the loop.</h2>
        <p className="lede" style={{ marginTop: 10 }}>
          Mend watches a data pipeline, detects schema drift as rows arrive, and proposes a
          narrowly-scoped repair. It reasons about the failure with a language model, argues against
          its own conclusion, and then stops — waiting for a person to approve the exact change
          before anything is modified.
        </p>
        <div className="steps-inline">
          <span className="step-chip">Detect without a model</span>
          <span className="step-chip">Diagnose with evidence</span>
          <span className="step-chip">Critique the diagnosis</span>
          <span className="step-chip">Human approves</span>
          <span className="step-chip">Verify the rerun</span>
          <span className="step-chip">Learn from the outcome</span>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>The incident sequence</h2>
            <p>Fixed order, enforced by Python — not chosen by the model at runtime.</p>
          </div>
          <span className="pill info">11 steps</span>
        </div>
        <ol className="timeline">
          {SEQUENCE.map(step => (
            <li key={step.title} className={step.cls || ""}>
              <div className="t-title">
                {step.title}
                <span className="t-tag">{step.tag}</span>
              </div>
              <p className="t-body">{step.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Why it can be trusted to stop</h2>
            <p>The guarantees that make an autonomous repair loop safe to point at real data.</p>
          </div>
        </div>
        <div className="guards">
          {GUARDS.map(guard => (
            <div className="guard" key={guard.title}>
              <h3>{guard.title}</h3>
              <p>{guard.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>It gets better each time</h2>
            <p>Outcomes become procedure, not just history.</p>
          </div>
          <span className="pill ok">Self-evolving</span>
        </div>
        <p className="lede">
          Incident memory records <em>what happened</em>. A second store — the procedural graph —
          records <em>what to do about it</em>. When a repair is verified, that fix type is
          reinforced for the error type. When a human rejects one, or a rerun fails to verify, it
          accumulates negative evidence and is eventually pruned.
        </p>
        <p className="lede" style={{ marginTop: 12 }}>
          Pruning is derived from the evidence counters, never asserted by the model, and the
          rewritten graph is only committed if it validates <em>and</em> the pipeline smoke test
          still passes. The result is rendered into both model prompts as evidence — which is why a
          diagnosis will often tell you a repair was “tried before and reverted.”
        </p>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>What is real, and what is simulated</h2>
            <p>Stated plainly, so a demo is never mistaken for production.</p>
          </div>
        </div>
        <table className="facts">
          <tbody>
            <tr>
              <td>Model reasoning</td>
              <td><strong>Real.</strong> Live provider calls for diagnosis and critique. No scripted fallback is ever substituted.</td>
            </tr>
            <tr>
              <td>Drift detection</td>
              <td><strong>Real.</strong> Values are type-checked against the expected schema on arrival, in the backend.</td>
            </tr>
            <tr>
              <td>Repair</td>
              <td><strong>Real, but isolated.</strong> Values are genuinely converted on a copy and re-validated. The original flagged record is preserved.</td>
            </tr>
            <tr>
              <td>Human approval</td>
              <td><strong>Real.</strong> Blocking, hash-bound, and expires if left unanswered.</td>
            </tr>
            <tr>
              <td>The pipeline itself</td>
              <td><strong>Simulated.</strong> A local fixture batch stands in for a warehouse. No production broker or sink is connected, so a passing rerun is not independent proof of a production repair.</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  );
}
