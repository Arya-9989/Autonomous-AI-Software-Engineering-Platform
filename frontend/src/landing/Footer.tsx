import { Link } from "react-router";

import { REPO_URL } from "./links";
import { sample } from "./sample";

export function Footer() {
  return (
    <footer className="bg-surface-2 text-[12px] leading-4 tracking-[-0.01em] text-ink-2">
      <div className="wrap border-t border-hairline py-6">
        <ol className="space-y-2">
          <li>
            Every example on this page comes from one run of the deterministic offline provider (
            <span className="font-mono">{sample.provider.model}</span>) on the spec shown. A test in the
            repository regenerates it and fails if the page and the pipeline ever disagree. Claude follows the
            same output contract, but its code differs from run to run.
          </li>
          <li>
            AutoSWE generates infrastructure code. It never runs{" "}
            <span className="font-mono">terraform apply</span> or deploys anything for you.
          </li>
        </ol>
        <div className="mt-6 flex flex-col gap-3 border-t border-hairline pt-5 sm:flex-row sm:items-center">
          <p>Copyright © 2026 Arya Mahindrakar. MIT License.</p>
          <nav aria-label="Footer" className="flex flex-wrap gap-x-5 sm:ml-auto">
            <Link to="/app" className="inline-flex min-h-11 items-center hover:text-ink hover:underline">
              App
            </Link>
            <a href="/api/docs" className="inline-flex min-h-11 items-center hover:text-ink hover:underline">
              API docs
            </a>
            <a href={REPO_URL} className="inline-flex min-h-11 items-center hover:text-ink hover:underline">
              Source on GitHub
            </a>
          </nav>
        </div>
      </div>
    </footer>
  );
}
