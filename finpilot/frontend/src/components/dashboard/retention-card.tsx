"use client";

/**
 * How long this upload is kept.
 *
 * Right after the analysis the user decides: delete everything now, or keep
 * it for 30 days. After that, a slim banner shows the deletion date with
 * "Extend 30 days" (Pro) and "Delete now". Emails go out 7 days and 1 day
 * before the date -- the banner says so, so nobody is surprised.
 */

import { CalendarClock, Crown, Download, Loader2, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";

import { useAccount } from "@/components/account/account-context";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ApiError, api, type JobState } from "@/lib/api";
import { cn } from "@/lib/utils";

function formatDay(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" });
}

export function RetentionCard({
  job,
  onChanged,
  onDeleted,
}: {
  job: JobState;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const { account, handleLimit } = useAccount();
  const [busy, setBusy] = useState<"keep" | "extend" | "delete" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const r = job.retention;
  if (!r || !r.expires_at) return null;

  const until = formatDay(r.expires_at);
  const isPro = account.plan === "pro";

  async function act(kind: "keep" | "extend" | "delete") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "keep") await api.keepUpload(job.job_id);
      if (kind === "extend") await api.extendUpload(job.job_id);
      if (kind === "delete") {
        await api.deleteJob(job.job_id);
        onDeleted();
        return;
      }
      onChanged();
    } catch (err) {
      if (!handleLimit(err)) setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const deleteButton = confirming ? (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm text-destructive">Erase everything now? This can&rsquo;t be undone.</span>
      <Button size="sm" variant="destructive" onClick={() => act("delete")} disabled={busy !== null}>
        {busy === "delete" && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
        Yes, delete
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={busy !== null}>
        Cancel
      </Button>
    </div>
  ) : (
    <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive" onClick={() => setConfirming(true)}>
      <Trash2 className="size-3.5" aria-hidden /> Delete now
    </Button>
  );

  // the decision, right after the analysis
  if (r.choice === null) {
    return (
      <Card className="fp-rise gap-4 border-primary/30 p-5 ring-1 ring-primary/15">
        <div className="flex items-start gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-600">
            <ShieldCheck className="size-5" aria-hidden />
          </span>
          <div>
            <p className="font-semibold">Your analysis is ready. What should we do with your data?</p>
            <p className="text-sm text-muted-foreground">
              To keep your bank transactions safe, we don&rsquo;t hold on to them. Choose now — you can change your
              mind until the date below.
            </p>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <button
            type="button"
            onClick={() => act("keep")}
            disabled={busy !== null}
            className="rounded-xl border-2 border-primary/50 bg-primary/5 p-4 text-left transition-colors hover:bg-primary/10"
          >
            <p className="flex items-center gap-2 font-medium">
              {busy === "keep" ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <CalendarClock className="size-4 text-primary" aria-hidden />}
              Keep for {r.retention_days} days
              <Badge variant="outline" className="ml-auto">Recommended</Badge>
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              Keep asking questions and making reports until <b className="text-foreground">{until}</b>. We&rsquo;ll
              email you 7 days and 1 day before everything is deleted.
            </p>
          </button>
          <div className="rounded-xl border p-4">
            <p className="flex items-center gap-2 font-medium">
              <Trash2 className="size-4 text-destructive" aria-hidden /> Delete everything now
            </p>
            <p className="mt-1 mb-3 text-sm text-muted-foreground">
              Erase the original files, transactions, habits, charts and reports from our servers right away.{" "}
              <a href={api.exportUrl(job.job_id, "finpilot_export.zip")} download className="inline-flex items-center gap-1 underline underline-offset-2">
                <Download className="size-3" aria-hidden />Download a copy first
              </a>
            </p>
            {deleteButton}
          </div>
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
      </Card>
    );
  }

  // the reminder, from then on
  const soon = (r.days_left ?? 99) <= 7;
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border px-4 py-2.5 text-sm",
        soon ? "border-amber-500/40 bg-amber-500/10" : "bg-muted/40",
      )}
    >
      <span className="flex items-center gap-2">
        <ShieldCheck className={cn("size-4", soon ? "text-amber-600" : "text-emerald-600")} aria-hidden />
        <span>
          Kept until <b>{until}</b>
          <span className="text-muted-foreground">
            {" "}· {r.days_left} day{r.days_left === 1 ? "" : "s"} left · then deleted from our servers
          </span>
        </span>
      </span>
      <div className="ml-auto flex flex-wrap items-center gap-1">
        <Button size="sm" variant={soon ? "default" : "outline"} onClick={() => act("extend")} disabled={busy !== null}>
          {busy === "extend" ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <Crown className="size-3.5" aria-hidden />}
          Extend {r.retention_days} days{!isPro && " (Pro)"}
        </Button>
        {deleteButton}
      </div>
      {error && <p className="w-full text-destructive">{error}</p>}
    </div>
  );
}
