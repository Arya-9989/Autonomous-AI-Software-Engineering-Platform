import { useEffect, useRef } from "react";

import { countFrame } from "./countFrame";

/**
 * A number that counts up once, the first time it scrolls into view (P6 "number moments").
 *
 * The HTML always carries the final value, so it is right without JavaScript, in a full-page
 * capture and under reduced motion. Only a number still below the fold when the page loads is
 * reset to zero, and it counts up when it arrives; one already on screen is left alone. The
 * last frame writes the exact value, and tabular figures keep the width steady while it counts.
 */
export function CountUp({ value, duration = 1100 }: { value: number; duration?: number }) {
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const element = ref.current;
    const text = element?.firstChild;
    if (!element || !(text instanceof Text) || value === 0) return;
    if (typeof IntersectionObserver === "undefined") return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (element.getBoundingClientRect().top < window.innerHeight) return;

    const final = String(value);
    let frame = 0;
    text.nodeValue = "0";
    element.dataset.counting = "";
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        observer.disconnect();
        const start = performance.now();
        const tick = (now: number) => {
          const shown = countFrame(value, (now - start) / duration);
          text.nodeValue = String(shown);
          if (shown === value) {
            delete element.dataset.counting;
            return;
          }
          frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);
      },
      { threshold: 0.6 },
    );
    observer.observe(element);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      text.nodeValue = final;
      delete element.dataset.counting;
    };
  }, [value, duration]);

  return (
    <span ref={ref} className="tabular-nums">
      {value}
    </span>
  );
}
