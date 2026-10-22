import { useQuery } from "@tanstack/react-query";
import {
  Check,
  ChevronRight,
  Copy,
  File,
  FileCode2,
  FileJson,
  FileText,
  Folder,
  FolderOpen,
  FolderTree,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { toast } from "../lib/toast";

import { api } from "../lib/api";
import { ancestors, buildTree, defaultFile, type TreeNode } from "../lib/fileTree";
import { formatBytes } from "../lib/format";
import { highlight } from "../lib/highlight";
import type { FileEntry } from "../lib/types";
import { Badge, EmptyState, ErrorState, Skeleton } from "./ui";
import { cx } from "../lib/cx";

const STAGE_LABEL: Record<string, string> = {
  architect: "architect",
  implement: "code generator",
  devops: "devops",
  review: "reviewer (repair)",
};

const CODE_LANGUAGES = new Set(["python", "hcl", "docker", "yaml", "toml", "bash"]);

function FileIcon({ file }: { file: FileEntry }) {
  const className = "size-4 shrink-0 text-ink-2";
  if (CODE_LANGUAGES.has(file.language)) return <FileCode2 className={className} aria-hidden />;
  if (file.language === "json") return <FileJson className={className} aria-hidden />;
  if (file.language === "markdown") return <FileText className={className} aria-hidden />;
  return <File className={className} aria-hidden />;
}

function TreeItem({
  node,
  depth,
  collapsed,
  toggle,
  selected,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  collapsed: Set<string>;
  toggle: (path: string) => void;
  selected: string | null;
  onSelect: (path: string) => void;
}) {
  const indent = { paddingLeft: `${depth * 14 + 8}px` };
  if (node.file) {
    const active = selected === node.path;
    return (
      <li>
        <button
          type="button"
          onClick={() => onSelect(node.path)}
          style={indent}
          aria-current={active ? "true" : undefined}
          className={cx(
            "flex w-full items-center gap-2 rounded-lg py-1.5 pr-2 text-left text-[13px] leading-[18px] transition-colors duration-200 pointer-coarse:py-2.5",
            active ? "bg-accent-soft font-medium text-accent-ink" : "text-ink hover:bg-ink/[0.05]",
          )}
        >
          <span className="w-3.5 shrink-0" />
          <FileIcon file={node.file} />
          <span className="truncate">{node.name}</span>
        </button>
      </li>
    );
  }
  const open = !collapsed.has(node.path);
  return (
    <li>
      <button
        type="button"
        onClick={() => toggle(node.path)}
        style={indent}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-lg py-1.5 pr-2 text-left text-[13px] leading-[18px] text-ink transition-colors duration-200 hover:bg-ink/[0.05] pointer-coarse:py-2.5"
      >
        <ChevronRight
          className={cx(
            "size-3.5 shrink-0 text-ink-2 transition-transform duration-200 ease-apple",
            open && "rotate-90",
          )}
          aria-hidden
        />
        {open ? (
          <FolderOpen className="size-4 shrink-0 text-accent-ink" aria-hidden />
        ) : (
          <Folder className="size-4 shrink-0 text-accent-ink" aria-hidden />
        )}
        <span className="truncate">{node.name}</span>
      </button>
      {open && (
        <ul>
          {node.children.map((child) => (
            <TreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              collapsed={collapsed}
              toggle={toggle}
              selected={selected}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function CodeView({ jobId, path }: { jobId: string; path: string }) {
  const file = useQuery({ queryKey: ["job", jobId, "files", path], queryFn: () => api.file(jobId, path) });
  const [highlighted, setHighlighted] = useState<{ source: string; html: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const content = file.data?.content;
  const language = file.data?.language;
  const html = highlighted && highlighted.source === content ? highlighted.html : null;

  useEffect(() => {
    if (content === undefined || language === undefined) return;
    let cancelled = false;
    highlight(content, language)
      .then((rendered) => !cancelled && setHighlighted({ source: content, html: rendered }))
      .catch(() => undefined); // plain-text fallback stays visible
    return () => {
      cancelled = true;
    };
  }, [content, language]);

  const copy = async () => {
    if (!file.data) return;
    try {
      await navigator.clipboard.writeText(file.data.content);
      setCopied(true);
      toast.success(`Copied ${path}`);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Copy failed. Select the text and copy it instead.");
    }
  };

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex min-h-12 flex-wrap items-center gap-2 border-b border-hairline px-4 py-2">
        <span className="min-w-0 truncate font-mono text-[12px] text-ink">{path}</span>
        {file.data && (
          <>
            <Badge>{formatBytes(file.data.size)}</Badge>
            <Badge tone="brand">{STAGE_LABEL[file.data.stage] ?? file.data.stage}</Badge>
            <button
              type="button"
              onClick={copy}
              className="hit-44 ml-auto inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[12px] text-ink-2 transition-colors hover:bg-ink/[0.06] hover:text-ink"
            >
              {copied ? (
                <Check className="size-3.5 text-ok" aria-hidden />
              ) : (
                <Copy className="size-3.5" aria-hidden />
              )}
              {copied ? "Copied" : "Copy"}
            </button>
          </>
        )}
      </div>
      {file.data?.description && (
        <p className="border-b border-hairline px-4 py-2 text-[12px] leading-4 text-ink-2">
          {file.data.description}
        </p>
      )}
      <div
        className="code-view scrollbar-thin max-h-[32rem] min-h-0 flex-1 overflow-auto bg-code md:max-h-none"
        tabIndex={0}
        role="region"
        aria-label={`Contents of ${path}`}
      >
        {file.isError ? (
          <div className="p-4">
            <ErrorState error={file.error} onRetry={() => void file.refetch()} />
          </div>
        ) : !file.data ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 12 }, (_, i) => (
              <Skeleton key={i} className="h-3.5" />
            ))}
          </div>
        ) : html ? (
          <div dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <pre className="px-5 text-ink">{file.data.content}</pre>
        )}
      </div>
    </div>
  );
}

export function FileExplorer({
  jobId,
  focus,
  running,
}: {
  jobId: string;
  focus?: string | null;
  running: boolean;
}) {
  const files = useQuery({ queryKey: ["job", jobId, "files"], queryFn: () => api.files(jobId) });
  const tree = useMemo(() => buildTree(files.data ?? []), [files.data]);
  const [selected, setSelected] = useState<string | null>(null);
  // Folders start expanded (generated projects are small); this tracks the ones the user closed.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [lastFocus, setLastFocus] = useState<string | null | undefined>(focus);

  if (focus !== lastFocus) {
    // A file was opened from elsewhere (e.g. a review finding): select it and reveal it.
    setLastFocus(focus);
    if (focus) {
      setSelected(focus);
      setCollapsed((previous) => new Set([...previous].filter((p) => !ancestors(focus).includes(p))));
    }
  }

  const active = selected ?? (files.data ? defaultFile(files.data) : null);

  const toggle = (path: string) =>
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  if (files.isError) return <ErrorState error={files.error} onRetry={() => void files.refetch()} />;
  if (files.isPending) {
    return (
      <div
        className="flex h-[38rem] overflow-hidden rounded-[18px] bg-surface shadow-[var(--shadow-tile)]"
        aria-busy="true"
        aria-label="Loading files"
      >
        <div className="hidden w-64 space-y-2.5 border-r border-hairline p-4 md:block">
          {Array.from({ length: 12 }, (_, i) => (
            <Skeleton key={i} className="h-3.5" />
          ))}
        </div>
        <div className="flex-1 space-y-2.5 p-5">
          {Array.from({ length: 16 }, (_, i) => (
            <Skeleton key={i} className="h-3.5" />
          ))}
        </div>
      </div>
    );
  }
  if (files.data.length === 0) {
    return (
      <EmptyState icon={<FolderTree />} title={running ? "No files yet" : "No files"}>
        {running ? "Files appear here as soon as each agent finishes." : "This job produced no files."}
      </EmptyState>
    );
  }

  return (
    <div className="flex flex-col overflow-hidden rounded-[18px] bg-surface shadow-[var(--shadow-tile)] md:h-[38rem] md:flex-row">
      <nav
        aria-label="Generated files"
        className="scrollbar-thin max-h-64 shrink-0 overflow-y-auto border-b border-hairline p-2 md:max-h-none md:w-64 md:border-r md:border-b-0"
      >
        <p className="t-eyebrow px-2 pt-2 pb-2 text-ink-2">{files.data.length} files</p>
        <ul>
          {tree.children.map((node) => (
            <TreeItem
              key={node.path}
              node={node}
              depth={0}
              collapsed={collapsed}
              toggle={toggle}
              selected={active}
              onSelect={setSelected}
            />
          ))}
        </ul>
      </nav>
      {active ? <CodeView jobId={jobId} path={active} /> : null}
    </div>
  );
}
