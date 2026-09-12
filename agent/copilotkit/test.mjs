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
