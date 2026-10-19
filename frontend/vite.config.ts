import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    watch: { usePolling: process.env.CHOKIDAR_USEPOLLING === "true" },
    proxy: {
      "/api": { target: process.env.API_PROXY_TARGET || "http://api:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "node",
  },
});
