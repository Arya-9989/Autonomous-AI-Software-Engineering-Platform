/**
 * Syntax highlighting off the main thread. Loading Shiki's grammars and tokenising a file are
 * long tasks (hundreds of milliseconds on a phone); in a worker they never block input or
 * scrolling. Messages: `{ id, code, language }` in, `{ id, html }` or `{ id, error }` out.
 */
import type { HighlighterCore } from "shiki/core";

const LANGS: Record<string, () => Promise<unknown>> = {
  python: () => import("@shikijs/langs/python"),
  toml: () => import("@shikijs/langs/toml"),
  yaml: () => import("@shikijs/langs/yaml"),
  json: () => import("@shikijs/langs/json"),
  markdown: () => import("@shikijs/langs/markdown"),
  hcl: () => import("@shikijs/langs/hcl"),
  docker: () => import("@shikijs/langs/docker"),
  bash: () => import("@shikijs/langs/bash"),
  ini: () => import("@shikijs/langs/ini"),
};

let highlighter: Promise<HighlighterCore> | null = null;
const loaded = new Set<string>();

function getHighlighter(): Promise<HighlighterCore> {
  highlighter ??= (async () => {
    const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([
      import("shiki/core"),
      import("shiki/engine/javascript"),
    ]);
    return createHighlighterCore({
      themes: [import("@shikijs/themes/github-light-default"), import("@shikijs/themes/github-dark-default")],
      langs: [],
      engine: createJavaScriptRegexEngine(),
    });
  })();
  return highlighter;
}

/** Highlight code to HTML; unknown languages render as escaped plain text. */
async function render(code: string, language: string): Promise<string> {
  const instance = await getHighlighter();
  const loader = LANGS[language];
  let lang = "text";
  if (loader) {
    if (!loaded.has(language)) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      await instance.loadLanguage((await loader()) as any);
      loaded.add(language);
    }
    lang = language;
  }
  return instance.codeToHtml(code, {
    lang,
    themes: { light: "github-light-default", dark: "github-dark-default" },
    defaultColor: "light",
  });
}

interface HighlightRequest {
  id: number;
  code: string;
  language: string;
}

const scope = self as unknown as {
  addEventListener(type: "message", listener: (event: MessageEvent<HighlightRequest>) => void): void;
  postMessage(message: unknown): void;
};

scope.addEventListener("message", (event) => {
  const { id, code, language } = event.data;
  render(code, language).then(
    (html) => scope.postMessage({ id, html }),
    (error: unknown) => scope.postMessage({ id, error: String(error) }),
  );
});
