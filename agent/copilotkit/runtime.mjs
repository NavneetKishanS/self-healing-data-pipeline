// A backend bridge Dev C can import into its chosen frontend server.
import { CopilotRuntime, InMemoryAgentRunner, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";

export function createPipelineHandler({
  agentUrl = process.env.PIPELINE_AGENT_URL || "http://127.0.0.1:8000/agent",
  fetchSession = fetch,
  fetchBackend = fetch,
} = {}) {
  const handlers = new Map();
  return async function handler(request) {
    // Verify with Python's Auth0 middleware before exposing any cached runtime state.
    let session;
    try {
      session = await fetchSession(new URL("/api/session", agentUrl), {
        headers: { Authorization: request.headers.get("authorization") || "" },
        signal: AbortSignal.timeout(10000), redirect: "error",
      });
    } catch {
      return Response.json({ error: "Agent authentication service unavailable" }, { status: 503 });
    }
    if (!session.ok) return Response.json({ error: "Unauthorized" }, { status: session.status === 403 ? 403 : 401 });
    const { sub } = await session.json();
    if (typeof sub !== "string" || !sub) return Response.json({ error: "Invalid session" }, { status: 401 });
    const path = new URL(request.url).pathname;
    if (/^\/api\/runs(?:\/[A-Za-z0-9_-]+(?:\/decision)?)?$/.test(path) || path === "/api/integrations") {
      // Same-origin frontend calls for approval and status; Python rechecks permissions/owner.
      return fetchBackend(new URL(path, agentUrl), {
        method: request.method,
        headers: { Authorization: request.headers.get("authorization") || "", "Content-Type": "application/json" },
        body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer(),
        signal: AbortSignal.timeout(10000), redirect: "error",
      });
    }
    if (!handlers.has(sub)) {
      if (handlers.size >= 100) return Response.json({ error: "Demo session limit reached" }, { status: 503 });
      const runtime = new CopilotRuntime({
        agents: { default: new HttpAgent({ url: agentUrl }) },
        runner: new InMemoryAgentRunner(),
      });
      handlers.set(sub, createCopilotRuntimeHandler({ runtime, basePath: "/api/copilotkit" }));
    }
    // CopilotKit forwards the caller's Authorization header to the Python agent.
    return handlers.get(sub)(request);
  };
}
