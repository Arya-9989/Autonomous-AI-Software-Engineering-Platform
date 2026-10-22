/** Apple's entrance curve, cubic-bezier(0.16, 1, 0.3, 1), approximated by an exponential ease-out. */
export function easeOutExpo(t: number): number {
  return t >= 1 ? 1 : 1 - Math.pow(2, -10 * t);
}

/**
 * The integer shown `progress` of the way through a count from 0 to `value` (progress 0..1+).
 * Never overshoots, and lands exactly on `value` once progress reaches 1.
 */
export function countFrame(value: number, progress: number): number {
  if (progress >= 1) return value;
  const t = Math.max(0, progress);
  return Math.min(value, Math.round(value * easeOutExpo(t)));
}
