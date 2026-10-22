import { useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { Dialog } from "radix-ui";
import { useState, type FormEvent } from "react";
import { toast } from "../lib/toast";

import { Button } from "../components/ui";
import { getApiKey, setApiKey } from "../lib/api";

export function ApiKeyDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [value, setValue] = useState("");
  const queryClient = useQueryClient();

  const save = (event: FormEvent) => {
    event.preventDefault();
    const key = value.trim() || null;
    setApiKey(key);
    void queryClient.invalidateQueries();
    toast.success(key ? "API key saved in this browser" : "API key removed");
    onClose();
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (next) setValue(getApiKey() ?? "");
        else onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/45 backdrop-blur-sm data-[state=open]:animate-[fade-in_200ms_ease-out]" />
        <Dialog.Content
          onOpenAutoFocus={() => setValue(getApiKey() ?? "")}
          className="fixed top-1/2 left-1/2 z-50 w-[min(30rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-[22px] bg-raised p-7 text-ink shadow-[0_24px_80px_rgb(0_0_0/0.35)] outline-none data-[state=open]:animate-[pop-in_240ms_cubic-bezier(0.25,0.1,0.25,1)]"
        >
          <form onSubmit={save}>
            <span className="grid size-11 place-items-center rounded-2xl bg-accent-soft text-accent-ink">
              <KeyRound className="size-5" aria-hidden />
            </span>
            <Dialog.Title className="mt-4 text-[21px] leading-[25px] font-semibold tracking-[0.011em]">
              API key
            </Dialog.Title>
            <Dialog.Description className="mt-1.5 text-[14px] leading-5 text-ink-2">
              This server requires an API key. It is stored in this browser only and sent as a bearer token.
            </Dialog.Description>
            <label
              htmlFor="api-key"
              className="mt-5 mb-1.5 block text-[12px] leading-4 font-medium text-ink-2"
            >
              Key
            </label>
            <input
              id="api-key"
              type="password"
              autoComplete="off"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="h-11 w-full rounded-xl bg-surface-2 px-3.5 font-mono text-[14px] text-ink ring-1 ring-hairline outline-none placeholder:text-ink-2 focus:ring-2 focus:ring-accent-ink"
              placeholder="A key from API_KEYS"
            />
            <div className="mt-7 flex justify-end gap-2">
              <Dialog.Close asChild>
                <Button variant="plain" size="sm">
                  Cancel
                </Button>
              </Dialog.Close>
              <Button variant="primary" size="sm" type="submit">
                Save key
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
