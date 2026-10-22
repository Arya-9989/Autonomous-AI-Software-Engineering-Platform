import { lazy } from "react";

/**
 * Toasts without putting sonner on the first-paint path: the library is a separate chunk,
 * requested when <Toaster /> first renders (just after start-up) and always loaded by the time
 * someone acts. Same call signatures as sonner's `toast`.
 */
type Sonner = typeof import("sonner");
type Toast = Sonner["toast"];

let sonner: Promise<Sonner> | null = null;
const load = () => (sonner ??= import("sonner"));

export const Toaster = lazy(() => load().then((module) => ({ default: module.Toaster })));

export const toast = Object.assign(
  (...args: Parameters<Toast>) => void load().then(({ toast: show }) => show(...args)),
  {
    success: (...args: Parameters<Toast["success"]>) =>
      void load().then(({ toast: show }) => show.success(...args)),
    error: (...args: Parameters<Toast["error"]>) =>
      void load().then(({ toast: show }) => show.error(...args)),
  },
);
