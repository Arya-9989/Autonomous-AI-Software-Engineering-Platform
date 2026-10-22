import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const backend = process.env.AUTOSWE_BACKEND ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/healthz": backend,
      "/metrics": backend,
    },
  },
  build: {
    target: "es2022",
    // Mermaid's layout engines are large but lazy-loaded only when a diagram is shown.
    chunkSizeWarningLimit: 1600,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
