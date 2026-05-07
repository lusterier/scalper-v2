/// <reference types="vitest" />
import path from "node:path";
import { fileURLToPath } from "node:url";

import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Dev-only proxy to analytics-api so /api/* and /events/* resolve without
// CORS friction. Production routing F5+ via nginx (per WG#6 + §16.6 +
// OQ-9=A). Operator MUST run analytics-api on http://127.0.0.1:8000
// before `pnpm dev` else fetch fails ECONNREFUSED.
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
      "/api": "http://127.0.0.1:8000",
      "/events": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.ts"],
  },
});
