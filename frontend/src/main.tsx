import "./index.css";

import { StrictMode } from "react";
import { createRoot, hydrateRoot } from "react-dom/client";

import { App } from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root element");

const app = (
  <StrictMode>
    <App />
  </StrictMode>
);
// The product page (/) is prerendered at build time (scripts/prerender.mjs); other routes
// arrive with an empty root.
if (root.hasChildNodes()) hydrateRoot(root, app);
else createRoot(root).render(app);
