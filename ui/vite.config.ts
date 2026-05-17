/// <reference types="vitest" />
import path from "node:path";
import { fileURLToPath } from "node:url";

import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Dev proxy for /api/* and /events/* (avoids CORS friction). Targets the
// nginx reverse proxy on 127.0.0.1:8080 (its `location /api/` block routes
// to analytics-api:8000 over the backend network). Changed 2026-05-17 (ops)
// from the old direct 127.0.0.1:8000 target: analytics-api is internal-only
// and does NOT publish a host port, so the direct target was ECONNREFUSED
// ("Failed to load bots"). nginx must be up (docker compose) before `pnpm
// dev`. Production routing F5+ via nginx (per WG#6 + §16.6 + OQ-9=A).
export default defineConfig({
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
  ],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    host: "0.0.0.0", // §16.2 dashboard LAN-bind; backend stays 127.0.0.1
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/events": { target: "http://127.0.0.1:8080", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.ts"],
  },
});
