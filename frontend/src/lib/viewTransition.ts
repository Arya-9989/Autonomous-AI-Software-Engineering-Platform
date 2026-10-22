import { flushSync } from "react-dom";

/**
 * Shared-element navigation (P7) with the View Transitions API: the clicked card's parts
 * (`data-vt="<name>"`) morph into the page's matching parts (`data-vt-target="<name>"`).
 *
 * Names are assigned only for the length of one transition, old side first, then the new side
 * inside the update, so no two elements ever share one (which would cancel the transition), and
 * a page loaded directly carries none. Without the API, or with reduced motion, `update` just
 * runs: the navigation is instant.
 */
export function canMorph(): boolean {
  return (
    typeof document !== "undefined" &&
    typeof document.startViewTransition === "function" &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

function named(root: ParentNode, attribute: "data-vt" | "data-vt-target"): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(`[${attribute}]`)];
}

export function morph(source: HTMLElement, update: () => void): void {
  if (!canMorph()) {
    update();
    return;
  }
  const from = [source, ...named(source, "data-vt")].filter((element) => element.dataset.vt);
  for (const element of from) element.style.viewTransitionName = element.dataset.vt!;
  let to: HTMLElement[] = [];
  const transition = document.startViewTransition(() => {
    for (const element of from) element.style.viewTransitionName = "";
    flushSync(update);
    to = named(document, "data-vt-target");
    for (const element of to) element.style.viewTransitionName = element.dataset.vtTarget!;
  });
  void transition.finished.finally(() => {
    for (const element of to) element.style.viewTransitionName = "";
  });
}
