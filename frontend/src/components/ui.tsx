import { AlertTriangle, Loader2, RotateCw } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { cx } from "../lib/cx";

import { SIZES, VARIANTS, type Variant } from "../lib/pill";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: keyof typeof SIZES;
  loading?: boolean;
  icon?: ReactNode;
}

/** Apple-style pill button: one filled primary, an outline secondary. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading = false, icon, className, children, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      className={cx(
        "inline-flex shrink-0 items-center justify-center gap-2 rounded-full whitespace-nowrap transition-[background-color,color,border-color,opacity] duration-200 ease-apple disabled:cursor-not-allowed",
        SIZES[size],
        VARIANTS[variant],
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  children: ReactNode;
  /** Which edge of the button the tooltip lines up with; "end" keeps it on screen at the right. */
  tip?: "start" | "center" | "end";
}

const TIP_ALIGN = {
  start: "left-0",
  center: "left-1/2 -translate-x-1/2",
  end: "right-0",
} as const;

/**
 * Icon-only button: the label is its accessible name and, for sighted mouse and keyboard
 * users, a small tooltip (after a short hover, or at once on keyboard focus). The tooltip is
 * pure CSS and aria-hidden, since the name is already announced; it is not shown on touch
 * screens. 44px hit target on touch.
 */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, children, className, tip = "center", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      className={cx(
        "group/tip relative inline-grid size-9 shrink-0 place-items-center rounded-full text-ink-2 transition-colors duration-200 ease-apple hover:bg-ink/[0.07] hover:text-ink pointer-coarse:size-11",
        className,
      )}
      {...props}
    >
      {children}
      <span
        aria-hidden
        className={cx(
          "pointer-events-none absolute top-full z-50 mt-1.5 hidden rounded-lg bg-ink px-2.5 py-1.5 text-[12px] leading-4 font-medium tracking-[-0.01em] whitespace-nowrap text-canvas shadow-lg",
          "group-hover/tip:block group-hover/tip:animate-[fade-in_160ms_ease-out_400ms_both] group-focus-visible/tip:block pointer-coarse:!hidden",
          TIP_ALIGN[tip],
        )}
      >
        {label}
      </span>
    </button>
  );
});

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cx("rounded-[18px] bg-surface shadow-[var(--shadow-tile)]", className)}>{children}</div>
  );
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h3 className="text-[17px] font-semibold tracking-[-0.022em] text-ink">{children}</h3>
      {action}
    </div>
  );
}

const TONES = {
  neutral: "bg-ink/[0.06] text-ink-2",
  brand: "bg-accent-soft text-accent-ink",
  success: "bg-ok-soft text-ok",
  warning: "bg-warn-soft text-warn",
  danger: "bg-bad-soft text-bad",
} as const;

export type Tone = keyof typeof TONES;

export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px] leading-4 font-medium tracking-[-0.01em]",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx("skeleton animate-shimmer rounded-lg", className)} />;
}

export function EmptyState({
  icon,
  title,
  children,
  action,
  heading: Heading = "h2",
}: {
  icon: ReactNode;
  title: string;
  heading?: "h1" | "h2" | "h3";
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      <div className="mb-5 grid size-14 place-items-center rounded-2xl bg-surface-2 text-ink-2 [&_svg]:size-6">
        {icon}
      </div>
      <Heading className="text-[21px] leading-[25px] font-semibold tracking-[0.011em] text-ink">
        {title}
      </Heading>
      {children && <div className="t-body mt-2 max-w-sm text-ink-2">{children}</div>}
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-[14px] bg-bad-soft p-4 text-[14px] leading-5 text-bad sm:flex-row sm:items-center"
    >
      <AlertTriangle className="size-5 shrink-0" aria-hidden />
      <p className="flex-1">{message}</p>
      {onRetry && (
        <Button size="sm" variant="danger" icon={<RotateCw className="size-3.5" />} onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
