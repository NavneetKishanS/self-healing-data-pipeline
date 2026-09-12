import { defineConfig } from "vite";

export default defineConfig({
  envDir: "..",
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/pipeline-api": {
        target: "http://127.0.0.1:8001", changeOrigin: true,
        rewrite: path => path.replace(/^\/pipeline-api/, "/api"),
        configure: proxy => proxy.on("proxyReq", (proxyReq, req) => {
          // Rewrite only our own development origin; foreign origins remain rejected.
          if (["http://127.0.0.1:5173", "http://localhost:5173"].includes(req.headers.origin)) {
            proxyReq.setHeader("Origin", "http://127.0.0.1:8001");
          }
        }),
      },
      // agent/server.py's AG-UI route is POST /agent, not /ag-ui - rewrite so the
      // frontend's HttpAgent URL doesn't need to match the backend path exactly.
      "/ag-ui": { target: "http://127.0.0.1:8000", changeOrigin: true,
                  rewrite: (path) => path.replace(/^\/ag-ui/, "/agent") },
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true }
    }
  }
});
