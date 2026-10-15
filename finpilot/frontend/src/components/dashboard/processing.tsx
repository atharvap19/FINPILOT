"use client";

/**
 * Shown while a job runs: what is happening, stage by stage, so the wait
 * reads as work being done rather than a spinner.
 */

import { Check, FileSearch, GitMerge, Link2, Loader2, ScanText, ShieldCheck, Sparkles, Table2, Wand2 } from "lucide-react";

import { Card } from "@/components/ui/card";
import type { JobState, Stage } from "@/lib/api";
import { cn } from "@/lib/utils";

const STAGES: Record<Stage, { label: string; detail: string; icon: typeof Check }> = {
  detect: { label: "Recognising your files", detail: "Which bank, which account, which period", icon: FileSearch },
  extract: { label: "Reading every row", detail: "Tables, PDF pages, emails and bills", icon: ScanText },
  map: { label: "Understanding the columns", detail: "Matching this layout to a known bank format", icon: Table2 },
  normalize: { label: "Cleaning up", detail: "Dates, ₹ amounts, merchant names — and masking account numbers", icon: Wand2 },
  validate: { label: "Checking the maths", detail: "Every running balance has to add up", icon: ShieldCheck },
  merge: { label: "Removing duplicates", detail: "The same payment seen in two files becomes one", icon: GitMerge },
  link: { label: "Connecting the dots", detail: "Transfers, refunds, receipts and bills matched to payments", icon: Link2 },
  done: { label: "Ready", detail: "", icon: Sparkles },
};

export function Processing({ job }: { job: JobState }) {
  const order: Stage[] = (job.stages.length ? job.stages : (Object.keys(STAGES) as Stage[])).filter((s) => s !== "done");
  const current = order.indexOf(job.stage);
  return (
    <Card className="fp-hero-bg relative mx-auto max-w-2xl overflow-hidden p-8">
      <div className="mb-6 flex items-center gap-4">
        <div className="fp-pulse flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
          <Loader2 className="size-6 animate-spin" />
        </div>
        <div>
          <p className="text-lg font-semibold">Building your expense guide…</p>
          <p className="text-sm text-muted-foreground">{job.files.length} file{job.files.length === 1 ? "" : "s"} · usually under a minute</p>
        </div>
        <p className="ml-auto text-2xl font-semibold tabular-nums text-primary">{Math.round(job.progress * 100)}%</p>
      </div>
      <div className="mb-6 h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-gradient-to-r from-indigo-500 via-violet-500 to-sky-500 transition-all duration-700" style={{ width: `${Math.max(4, job.progress * 100)}%` }} />
      </div>
      <ol className="space-y-2">
        {order.map((stage, i) => {
          const meta = STAGES[stage];
          const done = i < current;
          const active = i === current;
          const Icon = meta.icon;
          return (
            <li
              key={stage}
              className={cn(
                "flex items-center gap-3 rounded-xl px-3 py-2 transition-colors",
                active && "bg-background shadow-sm ring-1 ring-primary/20",
                !done && !active && "opacity-50",
              )}
            >
              <span className={cn("flex size-8 items-center justify-center rounded-lg", done ? "bg-emerald-500/15 text-emerald-600" : active ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground")}>
                {done ? <Check className="size-4" /> : <Icon className={cn("size-4", active && "animate-pulse")} />}
              </span>
              <div>
                <p className="text-sm font-medium">{meta.label}</p>
                {active && <p className="text-xs text-muted-foreground">{meta.detail}</p>}
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
