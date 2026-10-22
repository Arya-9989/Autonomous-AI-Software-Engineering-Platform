import { Link } from "react-router";

import { cx } from "../lib/cx";

/** The AutoSWE mark: five agents on one rising line; the last one lights up. */
export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="7.5" fill="#000" />
      <rect x=".5" y=".5" width="31" height="31" rx="7" fill="none" stroke="#fff" strokeOpacity=".16" />
      <path d="M7 22.5 25 9.5" stroke="#fff" strokeOpacity=".22" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="7" cy="22.5" r="2.1" fill="#6e6e73" />
      <circle cx="11.5" cy="19.25" r="2.1" fill="#8e8e93" />
      <circle cx="16" cy="16" r="2.1" fill="#c7c7cc" />
      <circle cx="20.5" cy="12.75" r="2.1" fill="#f5f5f7" />
      <circle cx="25" cy="9.5" r="3" fill="#7b6dff" />
    </svg>
  );
}

export function Logo({
  to = "/",
  className,
  wordmark = true,
}: {
  to?: string;
  className?: string;
  wordmark?: boolean;
}) {
  return (
    <Link
      to={to}
      className={cx("inline-flex min-h-11 items-center gap-2 rounded-md text-ink", className)}
      aria-label="AutoSWE home"
    >
      <Mark className="size-6" />
      {wordmark && <span className="text-[17px] font-semibold tracking-[-0.02em]">AutoSWE</span>}
    </Link>
  );
}
