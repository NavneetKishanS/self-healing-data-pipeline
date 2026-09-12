import { defineConfig } from "vite";

export default defineConfig({
  envDir: "..",
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/pipeline-api": {
        target: "http://127.0.0.1:8001", changeOrigin: true, ws: true,
        rewrite: path => path.replace(/^\/pipeline-api/, "/api"),
        configure: proxy => proxy.on("proxyReq", (proxyReq, req) => {
          // Rewrite only our own development origin; foreign origins remain rejected.
          if (["http://127.0.0.1:5173", "http://localhost:5173"].includes(req.headers.origin)) {
            proxyReq.setHeader("Origin", "http://127.0.0.1:8001");
          }
        }),
      },
      "/ag-ui": { target: "http://127.0.0.1:5050", changeOrigin: true },
      "/api": { target: "http://127.0.0.1:5050", changeOrigin: true }
    }
  }
});
