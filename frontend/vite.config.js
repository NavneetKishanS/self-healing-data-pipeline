import { defineConfig } from "vite";

export default defineConfig({
  envDir: "..",
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/ag-ui": { target: "http://127.0.0.1:5050", changeOrigin: true },
      "/api": { target: "http://127.0.0.1:5050", changeOrigin: true }
    }
  }
});
