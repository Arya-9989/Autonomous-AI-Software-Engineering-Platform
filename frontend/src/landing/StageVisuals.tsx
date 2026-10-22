import { File, Folder, Info } from "lucide-react";
import type { ReactNode } from "react";

import { buildTree, type TreeNode } from "../lib/fileTree";
import { formatBytes } from "../lib/format";
import { cx } from "../lib/cx";
import type { FileEntry } from "../lib/types";
import { filesByStage, sample } from "./sample";

/** Real output of each agent in the library example, drawn from sample.json. */

function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("rounded-[20px] bg-canvas p-5 ring-1 ring-hairline md:p-6", className)}>
      {children}
    </div>
  );
}

function Label({ children }: { children: ReactNode }) {
  return <p className="t-eyebrow mb-3 text-ink-2">{children}</p>;
}

const METHOD_TONE: Record<string, string> = {
  GET: "text-accent-ink",
  POST: "text-ok",
  PATCH: "text-warn",
  DELETE: "text-bad",
};

function Entities() {
  return (
    <Panel>
      <Label>Requirements · entities</Label>
      <div className="grid grid-cols-2 gap-3">
        {sample.requirements.entities.map((entity) => (
          <div key={entity.name} className="rounded-xl bg-surface-2 p-3.5">
            <p className="text-[17px] leading-6 font-semibold text-ink">{entity.name}</p>
            <ul className="mt-1.5 space-y-0.5 font-mono text-[12px] leading-[18px]">
              {entity.fields.map(([name, type, required]) => (
                <li key={name} className="flex justify-between gap-2">
                  <span className="min-w-0 break-all text-ink">
                    {name}
                    {!required && <span className="text-ink-2">?</span>}
                  </span>
                  <span className="text-accent-ink">{type}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <p className="mt-4 text-[14px] leading-5 text-ink-2">
        Plus {sample.requirements.user_stories.length} user stories and{" "}
        {sample.requirements.functional_requirements.length} functional requirements.
      </p>
    </Panel>
  );
}

function Design() {
  const shown = 6;
  const endpoints = sample.architecture.endpoints;
  return (
    <Panel>
      <Label>Architecture · {sample.architecture.style}</Label>
      <ul className="grid grid-cols-2 gap-2">
        {sample.architecture.components.map((component) => (
          <li key={component.name} className="rounded-xl bg-surface-2 px-3.5 py-2.5">
            <p className="text-[14px] leading-5 font-semibold text-ink">{component.name}</p>
            <p className="text-[12px] leading-4 text-ink-2">{component.technology}</p>
          </li>
        ))}
      </ul>
      <ul className="mt-4 space-y-1 font-mono text-[12px] leading-[18px]">
        {endpoints.slice(0, shown).map(([method, path]) => (
          <li key={`${method} ${path}`} className="flex gap-3">
            <span className={cx("w-14 shrink-0 font-semibold", METHOD_TONE[method] ?? "text-ink")}>
              {method}
            </span>
            <span className="truncate text-ink">{path}</span>
          </li>
        ))}
        <li className="text-ink-2">+ {endpoints.length - shown} more endpoints</li>
      </ul>
      <ul className="mt-4 space-y-1 text-[14px] leading-5">
        {sample.architecture.decisions.map((title, index) => (
          <li key={title} className="text-ink">
            <span className="mr-2 font-mono text-[12px] text-ink-2">
              ADR-{String(index + 1).padStart(3, "0")}
            </span>
            {title}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function TreeRows({ node, depth }: { node: TreeNode; depth: number }) {
  return (
    <>
      {node.children.map((child) => (
        <li key={child.path}>
          <div className="flex items-center gap-2 py-[3px]" style={{ paddingLeft: depth * 16 }}>
            {child.file ? (
              <File className="size-3.5 shrink-0 text-ink-2" aria-hidden />
            ) : (
              <Folder className="size-3.5 shrink-0 text-accent-ink" aria-hidden />
            )}
            <span className="truncate text-ink">{child.name}</span>
            {child.file && <span className="ml-auto pl-3 text-ink-2">{formatBytes(child.file.size)}</span>}
          </div>
          {!child.file && (
            <ul>
              <TreeRows node={child} depth={depth + 1} />
            </ul>
          )}
        </li>
      ))}
    </>
  );
}

function Tree({ stage, label }: { stage: string; label: string }) {
  const files = filesByStage(stage);
  const tree = buildTree(files.map((file) => ({ ...file, description: "" }) as FileEntry));
  return (
    <Panel>
      <Label>
        {label} · {files.length} files
      </Label>
      <ul className="font-mono text-[12px] leading-[18px]">
        <TreeRows node={tree} depth={0} />
      </ul>
    </Panel>
  );
}

function Review() {
  const { validation, review } = sample;
  return (
    <Panel>
      <Label>Review</Label>
      <div className="flex items-end gap-6">
        <p className="text-[64px] leading-[64px] font-semibold tracking-[-0.01em] text-ink tabular-nums">
          {review.score}
          <span className="text-[21px] leading-6 font-medium text-ink-2">/100</span>
        </p>
        <dl className="grid grid-cols-3 gap-5 pb-1">
          {[
            ["Files", validation.files_checked],
            ["Errors", validation.errors],
            ["Warnings", validation.warnings],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="text-[12px] leading-4 text-ink-2">{label}</dt>
              <dd className="text-[21px] leading-6 font-semibold text-ink tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
      </div>
      <ul className="mt-5 space-y-2.5">
        {review.findings.map((finding) => (
          <li key={finding.message} className="flex gap-3 rounded-xl bg-surface-2 p-3.5">
            <Info className="mt-0.5 size-4 shrink-0 text-accent-ink" aria-label="Note" />
            <div className="min-w-0 text-[14px] leading-5">
              <p className="text-ink">{finding.message}</p>
              <p className="mt-0.5 text-ink-2">{finding.suggestion}</p>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

const VISUALS = [
  () => <Entities />,
  () => <Design />,
  () => <Tree stage="implement" label="Code generator" />,
  () => <Tree stage="devops" label="DevOps engineer" />,
  () => <Review />,
];

export function StageVisual({ index }: { index: number }) {
  const Visual = VISUALS[index];
  return Visual ? <Visual /> : null;
}
