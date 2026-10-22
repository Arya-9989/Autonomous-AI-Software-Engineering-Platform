import { StrictMode } from "react";
import { prerenderToNodeStream } from "react-dom/static";

import { App } from "./App";

/**
 * Build-time render of one route to HTML (scripts/prerender.mjs); the client hydrates it.
 * `prerenderToNodeStream` waits for lazy chunks (the studio) to resolve, unlike renderToString.
 * Nothing has loaded yet at build time, so data-driven views render their loading states.
 */
export async function render(url: string): Promise<string> {
  const { prelude } = await prerenderToNodeStream(
    <StrictMode>
      <App location={url} />
    </StrictMode>,
  );
  const chunks: Buffer[] = [];
  for await (const chunk of prelude) chunks.push(Buffer.from(chunk as Uint8Array));
  return Buffer.concat(chunks).toString("utf8");
}
