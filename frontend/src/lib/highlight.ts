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
      themes: [import("@shikijs/themes/github-light"), import("@shikijs/themes/github-dark")],
      langs: [],
      engine: createJavaScriptRegexEngine(),
    });
  })();
  return highlighter;
}

/** Highlight code to HTML; unknown languages render as escaped plain text. */
export async function highlight(code: string, language: string): Promise<string> {
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
    themes: { light: "github-light", dark: "github-dark" },
    defaultColor: "light",
  });
}
