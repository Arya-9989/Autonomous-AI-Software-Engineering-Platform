import { readFileSync } from "node:fs";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

const backend = process.env.AUTOSWE_BACKEND ?? "http://127.0.0.1:8000";

interface Chunk {
  type: "chunk";
  fileName: string;
  imports: string[];
  isEntry: boolean;
  facadeModuleId?: string | null;
}

/**
 * Inlines boot.js into index.html (it must run before the first paint, and a separate request
 * would block rendering) and fills in the chunk lists it preloads: the lazily imported landing
 * page or studio, plus their static imports that the entry does not already load.
 */
function bootScript(): Plugin {
  const source = readFileSync(new URL("./boot.js", import.meta.url), "utf8")
    .split("\n")
    .filter((line) => !line.trimStart().startsWith("//"))
    .join("\n")
    .trim();
  return {
    name: "autoswe-boot-script",
    transformIndexHtml: {
      order: "post",
      handler(html, ctx) {
        let landing: string[] = [];
        let studio: string[] = [];
        if (ctx.bundle) {
          const all = Object.values(ctx.bundle).filter(
            (item): item is Chunk & typeof item => item.type === "chunk",
          );
          const byName = new Map(all.map((chunk) => [chunk.fileName, chunk]));
          const closure = (start: string[]) => {
            const seen = new Set<string>();
            const visit = (name: string) => {
              if (seen.has(name)) return;
              seen.add(name);
              byName.get(name)?.imports.forEach(visit);
            };
            start.forEach(visit);
            return seen;
          };
          const entry = closure(all.filter((chunk) => chunk.isEntry).map((chunk) => chunk.fileName));
          const lazyChunks = (module: string) => {
            const chunk = all.find((item) => item.facadeModuleId?.endsWith(module));
            if (!chunk) throw new Error(`boot script: no chunk for ${module}`);
            return [...closure([chunk.fileName])]
              .filter((name) => !entry.has(name))
              .map((name) => `/${name}`);
          };
          landing = lazyChunks("src/landing/LandingPage.tsx");
          studio = lazyChunks("src/app/AppShell.tsx");
        }
        const script = source
          .replace("__LANDING_CHUNKS__", JSON.stringify(landing))
          .replace("__STUDIO_CHUNKS__", JSON.stringify(studio));
        return html.replace('<script src="/theme-init.js"></script>', `<script>${script}</script>`);
      },
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), bootScript()],
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/healthz": backend,
      "/metrics": backend,
    },
  },
  // Module workers keep dynamic imports as separate chunks (Shiki loads one grammar per language).
  worker: { format: "es" },
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
