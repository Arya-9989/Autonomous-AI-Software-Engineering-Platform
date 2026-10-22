import { useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { getApiKey, setApiKey } from "../lib/api";
import { Button } from "./ui";

export function ApiKeyDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState("");
  const queryClient = useQueryClient();

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) {
      setValue(getApiKey() ?? "");
      element.showModal();
    } else if (!open && element.open) {
      element.close();
    }
  }, [open]);

  const save = (event: FormEvent) => {
    event.preventDefault();
    setApiKey(value.trim() || null);
    void queryClient.invalidateQueries();
    onClose();
  };

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-2xl border border-zinc-200 bg-white p-0 text-zinc-900 shadow-2xl backdrop:bg-zinc-950/50 backdrop:backdrop-blur-sm dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100"
      aria-labelledby="api-key-title"
    >
      <form onSubmit={save} className="p-6">
        <div className="mb-4 flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-brand-50 text-brand-600 dark:bg-brand-950/60 dark:text-brand-300">
            <KeyRound className="size-5" aria-hidden />
          </span>
          <div>
            <h2 id="api-key-title" className="text-base font-semibold">
              API key
            </h2>
            <p className="text-sm text-zinc-500 dark:text-zinc-400">This server requires an API key.</p>
          </div>
        </div>
        <label
          htmlFor="api-key"
          className="mb-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-400"
        >
          Key (stored in this browser only)
        </label>
        <input
          id="api-key"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="h-10 w-full rounded-lg border border-zinc-300 bg-white px-3 font-mono text-sm outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-500/15 dark:border-zinc-700 dark:bg-zinc-950"
          placeholder="Paste a key from API_KEYS"
        />
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit">
            Save key
          </Button>
        </div>
      </form>
    </dialog>
  );
}
