import type { Architecture } from "./types";

const clean = (text: string) => text.replace(/["[\](){}<>|`#;]/g, "").trim() || "component";

export function diagramSource(architecture: Architecture): string {
  const ids = new Map(architecture.components.map((c, i) => [c.name, `c${i}`]));
  const lines = ["flowchart LR"];
  architecture.components.forEach((c) =>
    lines.push(`  ${ids.get(c.name)}["${clean(c.name)}<br/>${clean(c.technology)}"]`),
  );
  architecture.components.forEach((c) =>
    c.depends_on.forEach((dep) => {
      if (ids.has(dep)) lines.push(`  ${ids.get(c.name)} --> ${ids.get(dep)}`);
    }),
  );
  return lines.join("\n");
}
