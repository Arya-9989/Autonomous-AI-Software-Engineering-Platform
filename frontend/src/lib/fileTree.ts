import type { FileEntry } from "./types";

export interface TreeNode {
  name: string;
  path: string;
  file?: FileEntry;
  children: TreeNode[];
}

/** Build a sorted directory tree (folders first, then files, both alphabetical). */
export function buildTree(files: FileEntry[]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let node = root;
    parts.forEach((part, index) => {
      const path = parts.slice(0, index + 1).join("/");
      let child = node.children.find((c) => c.name === part);
      if (!child) {
        child = { name: part, path, children: [] };
        node.children.push(child);
      }
      if (index === parts.length - 1) child.file = file;
      node = child;
    });
  }
  const sort = (node: TreeNode) => {
    node.children.sort((a, b) => {
      const aDir = a.file ? 1 : 0;
      const bDir = b.file ? 1 : 0;
      return aDir - bDir || a.name.localeCompare(b.name);
    });
    node.children.forEach(sort);
  };
  sort(root);
  return root;
}

/** Every ancestor directory of a path, e.g. "a/b/c.py" -> ["a", "a/b"]. */
export function ancestors(path: string): string[] {
  const parts = path.split("/").slice(0, -1);
  return parts.map((_, index) => parts.slice(0, index + 1).join("/"));
}

const PREFERRED = ["README.md", "ARCHITECTURE.md", "app/main.py"];

export function defaultFile(files: FileEntry[]): string | null {
  for (const candidate of PREFERRED) {
    if (files.some((f) => f.path === candidate)) return candidate;
  }
  return files[0]?.path ?? null;
}
