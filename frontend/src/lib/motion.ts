/** Shared motion tokens: Apple-style easing, short durations, no bounce on data. */
export const EASE = [0.4, 0, 0.6, 1] as const;
export const EASE_OUT = [0.25, 0.1, 0.25, 1] as const;
export const SPRING = { type: "spring", stiffness: 380, damping: 34, mass: 0.9 } as const;
/** Stage changes in the pipeline: a spring with a little life, well inside the UI limit (bounce <= 0.2). */
export const STAGE_SPRING = { type: "spring", bounce: 0.18, visualDuration: 0.42 } as const;

/**
 * Motion's animation and layout engines (domMax: layout and layoutId need it), fetched by
 * <LazyMotion> after the first paint instead of shipping in the page's own chunk.
 */
export const loadMotionFeatures = () => import("./motionFeatures").then((module) => module.default);
