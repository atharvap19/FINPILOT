"use client";

/**
 * The first screen after signing in (and whenever "New upload" is chosen):
 * a greeting, the four-step journey, what can be uploaded, a big drop target,
 * and a one-click demo for people who want to see it before trusting it with
 * their own statements.
 */

import { Bot, CheckCircle2, FileUp, Loader2, MessageSquarePlus, PlayCircle, ScanSearch, Upload, Wand2 } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { useAccount } from "@/components/account/account-context";
import { ExpenseChat } from "@/components/expenses/expense-chat";
import { FORMATS } from "@/components/onboarding/formats";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const ACCEPT = [".csv", ".tsv", ".xlsx", ".xls", ".pdf", ".eml", ".mbox"].join(",");

const JOURNEY = [
  { icon: FileUp, label: "Add sources" },
  { icon: Wand2, label: "Clean & connect" },
  { icon: ScanSearch, label: "See your habits" },
  { icon: Bot, label: "Ask & get a report" },
];

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}

export function Welcome({
  onFiles,
  onDemo,
  busy,
  hasUploads,
  onStartTyping,
  onTypedLedgerReady,
}: {
  onFiles: (files: File[]) => void;
  onDemo: () => void;
  busy: "upload" | "demo" | null;
  hasUploads: boolean;
  /** creates a ledger with no files for typed expenses; returns its id */
  onStartTyping: () => Promise<string>;
  /** open that ledger once the first expense is in */
  onTypedLedgerReady: (jobId: string) => void;
}) {
  const [typedLedger, setTypedLedger] = useState<string | null>(null);
  const { account } = useAccount();
  const [over, setOver] = useState(false);
  const [picked, setPicked] = useState<File[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const first = account.user.name.split(" ")[0];

  const take = useCallback((list: FileList | null) => {
    if (!list?.length) return;
    setPicked((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...Array.from(list).filter((f) => !seen.has(`${f.name}:${f.size}`))];
    });
  }, []);

  return (
    <div className="space-y-8">
      {/* greeting */}
      <section className="fp-hero-bg relative overflow-hidden rounded-3xl border bg-card px-6 py-10 sm:px-10">
        <div className="fp-grid-bg pointer-events-none absolute inset-0" />
        <div className="relative max-w-2xl space-y-3">
          <p className="fp-rise text-sm font-medium text-muted-foreground">{greeting()}, {first} 👋</p>
          <h1 className="fp-rise text-3xl font-semibold tracking-tight sm:text-4xl" style={{ animationDelay: "80ms" }}>
            {hasUploads ? "Add more sources to your " : "Let’s build your "}
            <span className="fp-gradient-text">expense guide</span>
          </h1>
          <p className="fp-rise text-muted-foreground" style={{ animationDelay: "160ms" }}>
            Add a few months of statements, bills and receipts. The more sources you add, the more FinPilot can connect — and the sharper your insights.
          </p>
        </div>
        <ol className="relative mt-8 grid gap-3 sm:grid-cols-4">
          {JOURNEY.map(({ icon: Icon, label }, i) => (
            <li
              key={label}
              className={cn(
                "fp-rise flex items-center gap-3 rounded-xl border bg-background/80 px-3 py-2.5 text-sm backdrop-blur",
                i === 0 && "border-primary/40 ring-2 ring-primary/15",
              )}
              style={{ animationDelay: `${240 + i * 90}ms` }}
            >
              <span className={cn("flex size-8 items-center justify-center rounded-lg", i === 0 ? "bg-primary text-primary-foreground fp-pulse" : "bg-muted text-muted-foreground")}>
                <Icon className="size-4" />
              </span>
              <span>
                <span className="block text-[11px] text-muted-foreground">Step {i + 1}</span>
                <span className="font-medium">{label}</span>
              </span>
            </li>
          ))}
        </ol>
      </section>

      <div className="grid gap-6 lg:grid-cols-5">
        {/* drop zone */}
        <Card
          className={cn(
            "fp-rise relative gap-0 overflow-hidden border-2 border-dashed p-0 transition-colors lg:col-span-3",
            over ? "border-primary bg-primary/5" : "border-muted-foreground/20",
          )}
          style={{ animationDelay: "200ms" }}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); if (!busy) take(e.dataTransfer.files); }}
        >
          <input
            ref={input}
            type="file"
            multiple
            accept={ACCEPT}
            className="sr-only"
            onChange={(e) => { take(e.target.files); e.target.value = ""; }}
          />
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 py-12 text-center">
            <div className={cn("flex size-16 items-center justify-center rounded-2xl bg-primary/10 text-primary transition-transform", over && "scale-110")}>
              <Upload className="size-7" />
            </div>
            <div>
              <p className="text-lg font-semibold">Drop your files here</p>
              <p className="text-sm text-muted-foreground">or choose them — you can add several at once</p>
              <p className="mt-2 text-xs text-muted-foreground">
                Your data is deleted from our servers 30 days after the analysis — or right away, if you prefer.
              </p>
            </div>
            <div className="flex flex-wrap justify-center gap-2">
              <Button onClick={() => input.current?.click()} disabled={busy !== null} variant={picked.length ? "outline" : "default"}>
                Choose files
              </Button>
              <Button variant="ghost" onClick={onDemo} disabled={busy !== null}>
                {busy === "demo" ? <Loader2 className="size-4 animate-spin" /> : <PlayCircle className="size-4" />}
                Try with demo data
              </Button>
            </div>
          </div>

          {picked.length > 0 && (
            <div className="border-t bg-muted/30 px-6 py-4">
              <div className="mb-3 flex items-center justify-between">
                <p className="text-sm font-medium">{picked.length} file{picked.length === 1 ? "" : "s"} ready</p>
                <button type="button" className="text-xs text-muted-foreground hover:text-foreground" onClick={() => setPicked([])}>Clear</button>
              </div>
              <ul className="mb-4 flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
                {picked.map((f) => (
                  <li key={`${f.name}:${f.size}`} className="flex items-center gap-1 rounded-md border bg-background px-2 py-1 text-xs">
                    <CheckCircle2 className="size-3 text-emerald-600" />
                    {f.name}
                  </li>
                ))}
              </ul>
              <Button className="w-full" size="lg" disabled={busy !== null} onClick={() => onFiles(picked)}>
                {busy === "upload" ? <Loader2 className="size-4 animate-spin" /> : <Wand2 className="size-4" />}
                Build my expense guide
              </Button>
            </div>
          )}
        </Card>

        {/* or just type */}
        <Card className="fp-rise gap-3 p-5 lg:col-span-3" style={{ animationDelay: "260ms" }}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="flex items-center gap-2 font-semibold">
                <MessageSquarePlus className="size-4 text-primary" aria-hidden />
                No statements? Just type what you spend
              </p>
              <p className="text-sm text-muted-foreground">
                Cash, UPI, anything — FinPilot builds your ledger from what you type.
              </p>
            </div>
            {typedLedger && (
              <Button size="sm" variant="outline" onClick={() => onTypedLedgerReady(typedLedger)}>
                Open my ledger
              </Button>
            )}
          </div>
          <ExpenseChat
            jobId={typedLedger}
            ensureJob={async () => {
              const id = await onStartTyping();
              setTypedLedger(id);
              return id;
            }}
          />
        </Card>

        {/* formats */}
        <div className="space-y-3 lg:col-span-2 lg:col-start-4 lg:row-span-2 lg:row-start-1">
          <p className="text-sm font-semibold">What you can add</p>
          {FORMATS.map(({ icon: Icon, title, exts, body }, i) => (
            <div key={title} className="fp-rise flex gap-3 rounded-xl border bg-card p-3" style={{ animationDelay: `${300 + i * 60}ms` }}>
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted"><Icon className="size-4 text-indigo-600 dark:text-indigo-400" /></span>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  <p className="text-sm font-medium">{title}</p>
                  {exts.map((e) => <span key={e} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">{e}</span>)}
                </div>
                <p className="text-xs text-muted-foreground">{body}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
