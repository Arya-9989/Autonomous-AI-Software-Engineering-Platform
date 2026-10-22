// Prerenders the routes that can paint before any JavaScript or data arrives, then the client
// hydrates them (src/main.tsx). FastAPI picks the document per route (src/autoswe/web.py):
//
//   index.html  /              the product page, complete
//   app.html    /app           the studio with the new-project form (providers still loading)
//   job.html    /app/jobs/:id  the studio with the job page's loading skeleton
//   shell.html  anything else  the empty template; the client router renders the page
//
// Run after `vite build` and the SSR build of src/entry-server.tsx into dist-ssr/.
import { readFileSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const dist = fileURLToPath(new URL("../dist/", import.meta.url));
const ssr = fileURLToPath(new URL("../dist-ssr/", import.meta.url));

const { render } = await import(pathToFileURL(`${ssr}entry-server.js`).href);
const template = readFileSync(`${dist}index.html`, "utf8");
const marker = '<div id="root"></div>';
if (!template.includes(marker)) throw new Error(`prerender: ${marker} not found in index.html`);

const pages = [
  { file: "index.html", url: "/", expect: "Deployable project out." },
  { file: "app.html", url: "/app", expect: "What should we build?" },
  { file: "job.html", url: "/app/jobs/prerender", expect: 'aria-label="Loading job"' },
];
writeFileSync(`${dist}shell.html`, template);
for (const page of pages) {
  const html = await render(page.url);
  if (!html.includes(page.expect)) throw new Error(`prerender: ${page.url} did not render`);
  if (html.includes("<script")) throw new Error(`prerender: ${page.url} emitted a script`);
  writeFileSync(`${dist}${page.file}`, template.replace(marker, `<div id="root">${html}</div>`));
  console.log(`prerendered ${page.url} -> ${page.file} (${(html.length / 1024).toFixed(0)} KB)`);
}
rmSync(ssr, { recursive: true, force: true });
