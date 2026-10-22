import { cx } from "./cx";

export type Variant = "primary" | "secondary" | "plain" | "danger";

export const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover disabled:opacity-50",
  secondary:
    "border border-accent-ink text-accent-ink hover:bg-accent-soft disabled:border-hairline-strong disabled:text-ink-2",
  plain: "text-ink-2 hover:bg-ink/[0.06] hover:text-ink disabled:opacity-50",
  danger: "border border-bad/60 text-bad hover:bg-bad-soft disabled:opacity-50",
};

export const SIZES = {
  sm: "h-9 px-4 text-[14px] tracking-[-0.016em] pointer-coarse:min-h-11",
  md: "h-11 px-[22px] text-[17px] tracking-[-0.022em]",
} as const;

/** Pill classes for links that look like buttons. */
export function pill(variant: Variant = "primary", size: keyof typeof SIZES = "md"): string {
  return cx(
    "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-full whitespace-nowrap transition-[background-color,color,border-color] duration-200 ease-apple",
    SIZES[size],
    VARIANTS[variant],
  );
}
