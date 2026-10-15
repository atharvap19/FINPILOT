"use client";

/**
 * Type what you spent, like a message: "lunch 250 cash, petrol 500 upi,
 * yesterday chai 40". Each piece becomes a ledger entry (with Undo); pieces
 * without an amount are reported back instead of guessed at.
 *
 * Used in two places: on the welcome screen (where the first message also
 * creates a ledger, for people with no statements to upload) and in the
 * dashboard's floating "Log a spend" panel.
 */

import { Loader2, MessageSquarePlus, Send, Undo2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";

import { useAccount } from "@/components/account/account-context";
import { Button } from "@/components/ui/button";
import { ApiError, api, formatRupees, type TypedExpense } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Other views (budget, overview) refresh when the ledger changes here. */
export const LEDGER_CHANGED = "finpilot:ledger-changed";

interface Turn {
  id: number;
  text: string;
  added: TypedExpense[];
  skipped: string[];
  error?: string;
  undone: string[];
}

const EXAMPLES = ["lunch 250 cash", "petrol 500 upi, auto 60", "yesterday groceries 1,240 gpay", "got salary 85,000"];

function when(iso: string): string {
  const today = new Date();
  const d = new Date(`${iso}T00:00:00`);
  const diff = Math.round((new Date(today.toDateString()).getTime() - d.getTime()) / 86400000);
  return diff === 0 ? "today" : diff === 1 ? "yesterday" : d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export function ExpenseChat({
  jobId,
  ensureJob,
  compact = false,
}: {
  /** the ledger to add to; null on the welcome screen until the first message */
  jobId: string | null;
  /** creates a ledger when there is none yet, and returns its id */
  ensureJob?: () => Promise<string>;
  compact?: boolean;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [spentToday, setSpentToday] = useState<number | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const { mutate } = useSWRConfig();
  const { handleLimit } = useAccount();

  const refreshToday = async (id: string | null) => {
    if (!id) return;
    try {
      setSpentToday((await api.typedExpenses(id)).spent);
    } catch {
      /* not critical */
    }
  };

  useEffect(() => {
    void refreshToday(jobId);
  }, [jobId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [turns]);

  const changed = (id: string) => {
    void refreshToday(id);
    void mutate(["insights", id]);
    void mutate((key) => Array.isArray(key) && key[0] === "preview");
    window.dispatchEvent(new CustomEvent(LEDGER_CHANGED, { detail: id }));
  };

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setBusy(true);
    setDraft("");
    const id = Date.now();
    try {
      const target = jobId ?? (ensureJob ? await ensureJob() : null);
      if (!target) throw new Error("No ledger to add to yet.");
      const result = await api.logExpenses(target, message);
      setTurns((prev) => [...prev, { id, text: message, added: result.added, skipped: result.skipped, undone: [] }]);
      if (result.added.length) changed(target);
    } catch (err) {
      if (handleLimit(err)) return;
      setTurns((prev) => [...prev, { id, text: message, added: [], skipped: [], undone: [],
        error: err instanceof ApiError ? err.message : String(err) }]);
    } finally {
      setBusy(false);
    }
  }

  async function undo(turn: Turn, entry: TypedExpense) {
    if (!jobId) return;
    try {
      await api.undoExpense(jobId, entry.txn_id);
      setTurns((prev) => prev.map((t) => (t.id === turn.id ? { ...t, undone: [...t.undone, entry.txn_id] } : t)));
      changed(jobId);
    } catch {
      /* already gone */
    }
  }

  return (
    <div className="flex h-full flex-col gap-3">
      {spentToday !== null && (
        <p className="text-xs text-muted-foreground">
          Logged today: <b className="text-foreground">{formatRupees(spentToday)}</b>
        </p>
      )}

      <div className={cn("flex-1 space-y-3 overflow-y-auto pr-1", compact ? "max-h-72" : "max-h-80 min-h-24")}>
        {turns.length === 0 && (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              Type what you spent, like a message. Say how you paid and when if you like.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setDraft(example)}
                  className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  {example}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((turn) => (
          <div key={turn.id} className="space-y-1.5">
            <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3 py-1.5 text-sm text-primary-foreground">
              {turn.text}
            </p>
            <div className="w-fit max-w-[95%] rounded-2xl rounded-bl-sm border bg-muted/40 px-3 py-2 text-sm">
              {turn.error ? (
                <p className="text-destructive">{turn.error}</p>
              ) : (
                <>
                  {turn.added.length > 0 && (
                    <ul className="space-y-1">
                      {turn.added.map((e) => {
                        const gone = turn.undone.includes(e.txn_id);
                        return (
                          <li key={e.txn_id} className={cn("flex items-center gap-2", gone && "opacity-50 line-through")}>
                            <span className={cn("font-medium", e.direction === "credit" && "text-emerald-600 dark:text-emerald-400")}>
                              {e.direction === "credit" ? "+" : ""}{formatRupees(e.amount)}
                            </span>
                            <span>{e.label}</span>
                            <span className="text-xs text-muted-foreground">
                              {e.mode !== "OTHER" ? `${e.mode.toLowerCase()} · ` : ""}{when(e.date)}
                            </span>
                            {!gone && (
                              <button type="button" onClick={() => void undo(turn, e)} title="Undo"
                                className="ml-auto rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground">
                                <Undo2 className="size-3.5" aria-hidden />
                                <span className="sr-only">Undo {e.label}</span>
                              </button>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  {turn.skipped.length > 0 && (
                    <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                      I couldn&rsquo;t find an amount in: {turn.skipped.map((s) => `“${s}”`).join(", ")}
                    </p>
                  )}
                  {turn.added.length > 0 && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      Added to your ledger{turn.added.length > 1 ? ` (${turn.added.length})` : ""}.
                    </p>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void send(draft);
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="e.g. lunch 250 cash, petrol 500 upi"
          maxLength={2000}
          aria-label="Type what you spent"
          className="h-10 min-w-0 flex-1 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
        />
        <Button type="submit" size="icon" className="size-10" disabled={busy || !draft.trim()}>
          {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Send className="size-4" aria-hidden />}
          <span className="sr-only">Add</span>
        </Button>
      </form>
    </div>
  );
}

/** The dashboard's floating "Log a spend" button and its panel. */
export function ExpenseLauncher({ jobId }: { jobId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="fixed right-4 bottom-4 z-40 flex flex-col items-end gap-3 sm:right-6 sm:bottom-6">
      {open && (
        <div className="fp-rise w-[min(92vw,380px)] rounded-2xl border bg-popover p-4 shadow-2xl">
          <div className="mb-2 flex items-center justify-between">
            <p className="font-semibold">Log a spend</p>
            <button type="button" onClick={() => setOpen(false)} className="text-sm text-muted-foreground hover:text-foreground">
              Close
            </button>
          </div>
          <ExpenseChat jobId={jobId} compact />
        </div>
      )}
      <Button size="lg" className="rounded-full shadow-lg" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <MessageSquarePlus className="size-4" aria-hidden />
        {open ? "Hide" : "Log a spend"}
      </Button>
    </div>
  );
}
