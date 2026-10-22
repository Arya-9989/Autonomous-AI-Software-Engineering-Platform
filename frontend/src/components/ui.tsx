import { AlertTriangle, Loader2, RotateCw } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cx } from "../lib/cx";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-brand-600 text-white shadow-sm shadow-brand-600/20 hover:bg-brand-500 disabled:bg-brand-600/60",
  secondary:
    "border border-zinc-200 bg-white text-zinc-800 shadow-xs hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800",
  ghost:
    "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/80 dark:hover:text-zinc-100",
  danger:
    "border border-red-200 bg-white text-red-700 hover:bg-red-50 dark:border-red-900/60 dark:bg-transparent dark:text-red-400 dark:hover:bg-red-950/40",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "sm" | "md";
  loading?: boolean;
  icon?: ReactNode;
}

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  icon,
  className,
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      type="button"
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-70",
        size === "sm" ? "h-8 px-3 text-xs" : "h-10 px-4 text-sm",
        VARIANTS[variant],
        className,
      )}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cx(
        "rounded-xl border border-zinc-200/80 bg-white shadow-xs dark:border-zinc-800 dark:bg-zinc-900/60",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{children}</h3>
      {action}
    </div>
  );
}

const TONES = {
  neutral: "bg-zinc-100 text-zinc-700 ring-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:ring-zinc-700",
  brand:
    "bg-brand-50 text-brand-700 ring-brand-200 dark:bg-brand-950/50 dark:text-brand-300 dark:ring-brand-800/60",
  success:
    "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-900",
  warning:
    "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-900",
  danger: "bg-red-50 text-red-700 ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900",
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
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-md bg-zinc-200/70 dark:bg-zinc-800", className)} />;
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cx("size-4 animate-spin text-brand-500", className)} aria-label="Loading" />;
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <div className="mb-4 grid size-12 place-items-center rounded-xl bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
        {icon}
      </div>
      <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{title}</p>
      {children && <div className="mt-1 max-w-sm text-sm text-zinc-500 dark:text-zinc-400">{children}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-xl border border-red-200 bg-red-50/70 p-4 text-sm text-red-800 sm:flex-row sm:items-center dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300"
    >
      <AlertTriangle className="size-5 shrink-0" aria-hidden />
      <p className="flex-1">{message}</p>
      {onRetry && (
        <Button size="sm" variant="secondary" icon={<RotateCw className="size-3.5" />} onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: string; icon?: ReactNode; count?: number; disabled?: boolean }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div
      role="tablist"
      className="scrollbar-thin -mx-1 flex gap-1 overflow-x-auto border-b border-zinc-200 px-1 dark:border-zinc-800"
    >
      {tabs.map((tab) => {
        const active = tab.id === value;
        return (
          <button
            key={tab.id}
            role="tab"
            type="button"
            aria-selected={active}
            disabled={tab.disabled}
            onClick={() => onChange(tab.id)}
            className={cx(
              "-mb-px inline-flex shrink-0 items-center gap-2 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40",
              active
                ? "border-brand-500 text-zinc-900 dark:text-zinc-50"
                : "border-transparent text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200",
            )}
          >
            {tab.icon}
            {tab.label}
            {tab.count !== undefined && (
              <span className="rounded-full bg-zinc-100 px-1.5 text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
