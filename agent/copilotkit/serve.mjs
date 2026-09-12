// Standalone loopback runtime. A frontend may instead import runtime.mjs directly.
import http from "node:http";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { createPipelineHandler } from "./runtime.mjs";

const handler = createPipelineHandler();
const port = Number(process.env.COPILOTKIT_PORT || 4000);
const server = http.createServer(async (req, res) => {
  try {
    let size = 0;
    const chunks = [];
    for await (const chunk of req) {
      size += chunk.length;
      if (size > 262144) { res.writeHead(413); res.end(); return; }
      chunks.push(chunk);
    }
    const body = chunks.length ? Buffer.concat(chunks) : undefined;
    const request = new Request(`http://127.0.0.1:${port}${req.url}`, {
      method: req.method, headers: req.headers, body,
    });
    const response = await handler(request);
    res.writeHead(response.status, Object.fromEntries(response.headers));
    if (response.body) await pipeline(Readable.fromWeb(response.body), res);
    else res.end();
  } catch {
    if (!res.headersSent) res.writeHead(500);
    res.end();
  }
});
server.listen(port, "127.0.0.1", () => console.log(`CopilotKit runtime: http://127.0.0.1:${port}/api/copilotkit`));
