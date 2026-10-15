"use client";

/**
 * What the agent did, in order: its plan (thought summaries) and every tool it
 * chose to call with a one-line result. Shown live while it works, and kept
 * folded under each answer afterwards.
 */

import {
  BarChart3,
  Brain,
  Check,
  ChevronDown,
  FileSearch,
  FileText,
  Layers,
  ListFilter,
  Loader2,
  Receipt,
  Repeat,
  Scale,
  Wallet,
} from "lucide-react";
import { useState } from "react";

import type { AgentEvent } from "@/lib/api";
import { cn } from "@/lib/utils";

export type Step =
  | { kind: "thought"; text: string }
  | { kind: "tool"; id: number; name: string; label: string; summary?: string };

/** Fold the event stream into the list of steps it describes. */
export function addStep(steps: Step[], event: AgentEvent): Step[] {
  if (event.type === "thought") return [...steps, { kind: "thought", text: event.text }];
  if (event.type === "tool_call")
    return [...steps, { kind: "tool", id: event.id, name: event.name, label: event.label }];
  if (event.type === "tool_result")
    return steps.map((s) =>
      s.kind === "tool" && s.id === event.id ? { ...s, summary: event.summary } : s,
    );
  return steps;
}

const ICONS: Record<string, typeof Wallet> = {
  get_financial_overview: Wallet,
  list_segments: Layers,
  get_segment_details: Layers,
  search_transactions: ListFilter,
  aggregate_spending: BarChart3,
  compare_periods: Scale,
  find_recurring_payments: Repeat,
  get_bills: Receipt,
  list_documents: FileText,
  search_documents: FileSearch,
};

/** Thought summaries arrive as markdown with a bold title line; show the title. */
function thoughtTitle(text: string): string {
  const bold = text.match(/\*\*(.+?)\*\*/);
  const line = (bold?.[1] ?? text.split("\n")[0]).trim();
  return line.length > 110 ? `${line.slice(0, 110)}…` : line;
}

export function AgentSteps({
  steps,
  running,
  defaultOpen = false,
}: {
  steps: Step[];
  running: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const tools = steps.filter((s) => s.kind === "tool").length;
  if (!steps.length && !running) return null;
  const expanded = open || running;

  return (
    <div className="rounded-lg border bg-muted/30 text-xs">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-muted-foreground"
        aria-expanded={expanded}
      >
        {running ? (
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
        ) : (
          <Check className="size-3.5 text-emerald-600" aria-hidden />
        )}
        <span className="font-medium text-foreground">
          {running ? "Working…" : `Investigated with ${tools} tool call${tools === 1 ? "" : "s"}`}
        </span>
        {!running && (
          <ChevronDown
            className={cn("ml-auto size-3.5 transition-transform", expanded && "rotate-180")}
            aria-hidden
          />
        )}
      </button>
      {expanded && (
        <ol className="space-y-1.5 border-t px-3 py-2">
          {steps.map((step, index) => {
            if (step.kind === "thought") {
              return (
                <li key={`t-${index}`} className="flex gap-2 text-muted-foreground">
                  <Brain className="mt-0.5 size-3.5 shrink-0 text-violet-500" aria-hidden />
                  <span className="italic">{thoughtTitle(step.text)}</span>
                </li>
              );
            }
            const Icon = ICONS[step.name] ?? ListFilter;
            return (
              <li key={`c-${step.id}`} className="flex gap-2">
                <Icon className="mt-0.5 size-3.5 shrink-0 text-sky-600" aria-hidden />
                <span className="min-w-0">
                  <span className="text-foreground">{step.label}</span>
                  {step.summary ? (
                    <span className="text-muted-foreground"> — {step.summary}</span>
                  ) : (
                    <Loader2
                      className="ml-1.5 inline size-3 animate-spin text-muted-foreground"
                      aria-hidden
                    />
                  )}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
