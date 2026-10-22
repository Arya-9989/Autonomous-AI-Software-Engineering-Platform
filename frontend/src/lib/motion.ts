/** Shared motion tokens: Apple-style easing, short durations, no bounce on data. */
export const EASE = [0.4, 0, 0.6, 1] as const;
export const EASE_OUT = [0.25, 0.1, 0.25, 1] as const;
export const SPRING = { type: "spring", stiffness: 380, damping: 34, mass: 0.9 } as const;
