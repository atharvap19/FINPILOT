"use client";

/**
 * The financial report: the agent investigates the whole ledger -- every
 * segment, recurring payments, bills, month-on-month change, and the documents
 * behind them -- and writes a report the user can download as a PDF.
 *
 * Generation takes a minute or two, so the agent's steps stream in while it
 * works. The last report is kept per job and reloads with the page.
 */

import {
  ChevronDown,
  Download,
  FileDown,
  FileText,
  Loader2,
  RefreshCw,
  ScrollText,
} from "lucide-react";
import { useRef, useState } from "react";
import useSWR from "swr";

import { useAccount } from "@/components/account/account-context";
import { addStep, AgentSteps, type Step } from "@/components/sources/agent-steps";
import { Markdown } from "@/components/sources/markdown";
import { useSegmentChart } from "@/components/sources/use-job";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, api, type FinancialReport } from "@/lib/api";
import { cn } from "@/lib/utils";

export function ReportCard({ jobId, ready }: { jobId: string; ready: boolean }) {
  const { data: saved, mutate } = useSWR<FinancialReport | null>(
    ready ? ["financial-report", jobId] : null,
    async () => {
      try {
        return await api.financialReport(jobId);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
    { revalidateOnFocus: false },
  );
  const { needsRun: notSegmented } = useSegmentChart(jobId, ready);
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const { handleLimit, refresh: refreshAccount } = useAccount();

  if (!ready) return null;

  async function generate() {
    setRunning(true);
    setError(null);
    setSteps([]);
    let collected: Step[] = [];
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await api.generateReport(
        jobId,
        (event) => {
          collected = addStep(collected, event);
          setSteps(collected);
          if (event.type === "report") {
            void mutate({ markdown: event.markdown, meta: event.meta }, { revalidate: false });
            void refreshAccount();
            setExpanded(false);
          } else if (event.type === "error") {
            setError(event.message);
          }
        },
        controller.signal,
      );
    } catch (err) {
      if (!controller.signal.aborted && !handleLimit(err)) {
        setError(err instanceof ApiError ? err.message : String(err));
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  }

  const meta = saved?.meta;
  const generatedAt = meta?.generated_at
    ? new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(
        new Date(meta.generated_at),
      )
    : null;

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2">
            <ScrollText className="size-4 text-sky-600" aria-hidden />
            Financial report
          </CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            {saved && generatedAt
              ? `Generated ${generatedAt} by ${meta?.model} after ${meta?.steps} tool calls.`
              : "The agent reviews every segment, your recurring payments, bills and documents, then writes a report you can download."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {saved && !running && (
            <>
              {meta?.pdf && (
                <Button asChild size="sm">
                  <a href={api.financialReportUrl(jobId, "pdf")} download>
                    <FileDown className="size-3.5" aria-hidden />
                    Download PDF
                  </a>
                </Button>
              )}
              <Button asChild size="sm" variant="outline">
                <a href={api.financialReportUrl(jobId, "md")} download>
                  <Download className="size-3.5" aria-hidden />
                  Markdown
                </a>
              </Button>
            </>
          )}
          {running ? (
            <Button size="sm" variant="outline" onClick={() => abortRef.current?.abort()}>
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
              Cancel
            </Button>
          ) : (
            <Button size="sm" variant={saved ? "outline" : "default"} onClick={generate}>
              {saved ? (
                <RefreshCw className="size-3.5" aria-hidden />
              ) : (
                <FileText className="size-3.5" aria-hidden />
              )}
              {saved ? "Regenerate" : "Generate report"}
            </Button>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        {notSegmented && !saved && !running && (
          <p className="text-xs text-muted-foreground">
            Tip: run <span className="font-medium">Find segments</span> first — the report
            explains each spending habit it finds.
          </p>
        )}

        {(running || (steps.length > 0 && error)) && (
          <AgentSteps steps={steps} running={running} defaultOpen />
        )}
        {running && (
          <p className="text-xs text-muted-foreground">
            This usually takes one to two minutes.
          </p>
        )}

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {saved && !running && (
          <div className="rounded-lg border">
            <div
              className={cn(
                "relative overflow-hidden px-5 py-3",
                !expanded && "max-h-96",
              )}
            >
              <Markdown text={saved.markdown} jobId={jobId} />
              {!expanded && (
                <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-card to-transparent" />
              )}
            </div>
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="flex w-full items-center justify-center gap-1 border-t py-2 text-xs text-muted-foreground hover:text-foreground"
            >
              {expanded ? "Collapse" : "Read the full report"}
              <ChevronDown
                className={cn("size-3.5 transition-transform", expanded && "rotate-180")}
                aria-hidden
              />
            </button>
          </div>
        )}
        {saved && !running && (
          <p className="text-[11px] leading-snug text-muted-foreground">
            Generated automatically by AI from your documents. For information only &mdash; not financial, tax or
            investment advice. FinPilot is not a SEBI-registered investment adviser.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
