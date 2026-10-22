import { useEffect } from "react";

/**
 * Quiet scroll reveals for elements marked `data-reveal`.
 *
 * Nothing is hidden on load. When the reader first scrolls, elements that are still entirely
 * below the viewport are armed (faded and lowered by 20px) and then revealed once, as they
 * enter. Content above the fold, full-page captures and reduced-motion readers never see an
 * empty state.
 */
export function useScrollReveal(root: React.RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const container = root.current;
    if (!container || typeof IntersectionObserver === "undefined") return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add("reveal-in");
          entry.target.classList.remove("reveal-armed");
          observer.unobserve(entry.target);
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.05 },
    );

    const arm = () => {
      window.removeEventListener("scroll", arm);
      const fold = window.innerHeight;
      container.querySelectorAll<HTMLElement>("[data-reveal]").forEach((element) => {
        if (element.getBoundingClientRect().top > fold) {
          element.classList.add("reveal-armed");
          observer.observe(element);
        }
      });
    };
    window.addEventListener("scroll", arm, { passive: true });
    return () => {
      window.removeEventListener("scroll", arm);
      observer.disconnect();
    };
  }, [root]);
}
