// Writes Brotli (.br) and gzip (.gz) copies of the built text assets and HTML pages.
// FastAPI serves the smallest one the browser accepts (src/autoswe/web.py), so nothing is
// compressed per request. Uses only node:zlib, at maximum quality: this runs once per build.
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";

const dist = fileURLToPath(new URL("../dist/", import.meta.url));
const targets = [
  ...readdirSync(join(dist, "assets")).map((name) => join(dist, "assets", name)),
  ...readdirSync(dist)
    .filter((name) => name.endsWith(".html"))
    .map((name) => join(dist, name)),
];
const COMPRESSIBLE = /\.(js|mjs|css|svg|json|txt|html|map)$/;
const MIN_BYTES = 1024;

let original = 0;
let brotli = 0;
let files = 0;
for (const path of targets) {
  if (!COMPRESSIBLE.test(path) || statSync(path).size < MIN_BYTES) continue;
  const source = readFileSync(path);
  const br = brotliCompressSync(source, {
    params: {
      [constants.BROTLI_PARAM_QUALITY]: constants.BROTLI_MAX_QUALITY,
      [constants.BROTLI_PARAM_SIZE_HINT]: source.length,
    },
  });
  const gz = gzipSync(source, { level: 9 });
  // A variant that is not smaller is pointless to send.
  if (br.length < source.length) writeFileSync(`${path}.br`, br);
  if (gz.length < source.length) writeFileSync(`${path}.gz`, gz);
  original += source.length;
  brotli += Math.min(br.length, source.length);
  files += 1;
}
const kb = (bytes) => `${(bytes / 1024).toFixed(0)} KB`;
console.log(`compressed ${files} assets: ${kb(original)} -> ${kb(brotli)} with Brotli`);
