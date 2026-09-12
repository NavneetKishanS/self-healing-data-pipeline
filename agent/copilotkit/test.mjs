import test from "node:test";
import assert from "node:assert/strict";
import { createPipelineHandler } from "./runtime.mjs";

test("runtime discovery requires a verified session", async () => {
  const handler = createPipelineHandler({ fetchSession: async () => new Response(null, { status: 401 }) });
  const response = await handler(new Request("http://localhost/api/copilotkit/info"));
  assert.equal(response.status, 401);
});

test("verified user can discover the actual registered AG-UI agent", async () => {
  const handler = createPipelineHandler({ fetchSession: async () => Response.json({ sub: "test-user" }) });
  const response = await handler(new Request("http://localhost/api/copilotkit/info", {
    headers: { Authorization: "Bearer test" },
  }));
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.ok(JSON.stringify(body).includes("default"));
});

test("approval proxy preserves the user's identity and exact decision", async () => {
  let forwarded;
  const handler = createPipelineHandler({
    fetchSession: async () => Response.json({ sub: "test-user" }),
    fetchBackend: async (url, options) => {
      forwarded = { url: String(url), options };
      return Response.json({ recorded: true });
    },
  });
  const body = JSON.stringify({ approved: false, fix_hash: "current-hash" });
  const response = await handler(new Request("http://localhost/api/runs/run1/decision", {
    method: "POST", headers: { Authorization: "Bearer user-token", "Content-Type": "application/json" }, body,
  }));
  assert.equal(response.status, 200);
  assert.equal(forwarded.url, "http://127.0.0.1:8000/api/runs/run1/decision");
  assert.equal(forwarded.options.headers.Authorization, "Bearer user-token");
  assert.equal(new TextDecoder().decode(forwarded.options.body), body);
});
