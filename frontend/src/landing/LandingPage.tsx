import { useEffect, useRef } from "react";

import { useScrollReveal } from "../lib/reveal";
import { Footer } from "./Footer";
import { GetStarted } from "./GetStarted";
import { Hero } from "./Hero";
import { Highlights } from "./Highlights";
import { GlobalNav, LocalNav } from "./Nav";
import { Output } from "./Output";
import { Story } from "./Story";
import { Validation } from "./Validation";

export default function LandingPage() {
  const root = useRef<HTMLDivElement>(null);
  useScrollReveal(root);
  useEffect(() => {
    document.title = "AutoSWE · Plain English in. Deployable project out.";
  }, []);

  return (
    <div ref={root} className="min-h-dvh bg-canvas">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-full focus:bg-accent focus:px-4 focus:py-2 focus:text-white"
      >
        Skip to content
      </a>
      <GlobalNav />
      <LocalNav />
      <main id="main" tabIndex={-1} className="outline-none">
        <Hero />
        <Highlights />
        <Story />
        <Output />
        <Validation />
        <GetStarted />
      </main>
      <Footer />
    </div>
  );
}
