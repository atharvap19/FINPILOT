"use client";

/**
 * The signed-in app.
 *
 *   no upload selected  -> Welcome (greeting, formats, drop zone, demo)
 *   upload processing   -> Processing (what is happening, stage by stage)
 *   upload ready        -> the dashboard, in tabs
 *
 * The chat and report tabs stay mounted while hidden, so a conversation or a
 * report in progress survives a look at the charts.
 */

import {
  AlertCircle,
  Bot,
  ChartPie,
  FolderOpen,
  LayoutDashboard,
  PiggyBank,
  ListOrdered,
  Plus,
  ScrollText,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import useSWR from "swr";

import { useAccount } from "@/components/account/account-context";
import { BudgetPlanner } from "@/components/dashboard/budget-planner";
import { HabitsView } from "@/components/dashboard/habits";
import { Overview } from "@/components/dashboard/overview";
import { Processing } from "@/components/dashboard/processing";
import { RetentionCard } from "@/components/dashboard/retention-card";
import { ExpenseLauncher } from "@/components/expenses/expense-chat";
import { Welcome } from "@/components/onboarding/welcome";
import { AgentChat } from "@/components/sources/agent-chat";
import { FileList } from "@/components/sources/file-list";
import { MappingDialog } from "@/components/sources/mapping-dialog";
import { ObligationsCard } from "@/components/sources/obligations-card";
import { PasswordDialog } from "@/components/sources/password-dialog";
import { PipelineStepper } from "@/components/sources/pipeline-stepper";
import { PreviewTable } from "@/components/sources/preview-table";
import { ReportCard } from "@/components/sources/report-card";
import { SummaryCard } from "@/components/sources/summary-card";
import { TransactionPanel } from "@/components/sources/transaction-panel";
import { useJob, useObligations, useRememberedJobId, useSummary } from "@/components/sources/use-job";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  ApiError,
  api,
  formatDate,
  type FileStatus,
  type JobListItem,
  type PendingMapping,
  type TransactionRow,
} from "@/lib/api";
import { LEGAL } from "@/lib/legal";
import { cn } from "@/lib/utils";

const TABS = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "budget", label: "Budget", icon: PiggyBank },
  { id: "habits", label: "Spending habits", icon: ChartPie },
  { id: "transactions", label: "Transactions", icon: ListOrdered },
  { id: "ask", label: "Ask FinPilot", icon: Bot },
  { id: "report", label: "Report", icon: ScrollText },
  { id: "files", label: "Files & exports", icon: FolderOpen },
] as const;
type TabId = (typeof TABS)[number]["id"];

export function SourcesPage() {
  const [jobId, setJobId] = useRememberedJobId();
  const [busy, setBusy] = useState<"upload" | "demo" | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<TransactionRow | null>(null);
  const [unlocking, setUnlocking] = useState<FileStatus | null>(null);
  const [mappingFor, setMappingFor] = useState<PendingMapping | null>(null);
  const [tab, setTab] = useState<TabId>("overview");
  const [seed, setSeed] = useState<{ id: number; text: string } | null>(null);

  const { handleLimit, refresh: refreshAccount } = useAccount();
  const { data: jobs, mutate: refreshJobs } = useSWR("my-jobs", () => api.jobs(), {
    revalidateOnFocus: false,
  });
  const { job, error: jobError, refresh, finished } = useJob(jobId);
  const summary = useSummary(jobId, finished);
  const obligations = useObligations(jobId, finished);

  // A job remembered in this browser may belong to a different account (or
  // have been deleted). Forget it rather than show "could not reach the job".
  useEffect(() => {
    if (jobs && jobId && !jobs.some((j) => j.job_id === jobId)) setJobId(null);
  }, [jobs, jobId, setJobId]);

  const open = useCallback(
    (id: string | null) => {
      setJobId(id);
      setSelected(null);
      setTab("overview");
      setSeed(null);
    },
    [setJobId],
  );

  const start = useCallback(
    async (kind: "upload" | "demo", files: File[] = []) => {
      setBusy(kind);
      setUploadError(null);
      try {
        const created = kind === "demo" ? await api.demo() : await api.createJob(files);
        await refreshJobs();
        open(created.job_id);
        void refreshAccount();
      } catch (err) {
        if (!handleLimit(err)) setUploadError(err instanceof ApiError ? err.message : String(err));
      } finally {
        setBusy(null);
      }
    },
    [open, refreshJobs, refreshAccount, handleLimit],
  );

  const openMapping = useCallback(
    async (file: FileStatus) => {
      if (!jobId) return;
      const pending = await api.pendingMappings(jobId);
      setMappingFor(pending.find((p) => p.doc_id === file.doc_id) ?? pending[0] ?? null);
    },
    [jobId],
  );

  const ask = (text: string) => {
    setSeed({ id: Date.now(), text });
    setTab("ask");
  };

  const current = jobs?.find((j) => j.job_id === jobId);
  const running = job && (job.status === "queued" || job.status === "running");

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-6 py-8">
      {uploadError && (
        <Alert variant="destructive">
          <AlertCircle className="size-4" aria-hidden />
          <AlertTitle>Upload failed</AlertTitle>
          <AlertDescription>{uploadError}</AlertDescription>
        </Alert>
      )}

      {!jobId ? (
        <>
          {jobs && jobs.length > 0 && <UploadSwitcher jobs={jobs} jobId={null} onOpen={open} />}
          <Welcome
            onFiles={(files) => void start("upload", files)}
            onDemo={() => void start("demo")}
            busy={busy}
            hasUploads={Boolean(jobs?.length)}
            onStartTyping={async () => {
              const { job_id } = await api.startManualLedger();
              await refreshJobs();
              return job_id;
            }}
            onTypedLedgerReady={(id) => open(id)}
          />
        </>
      ) : (
        <>
          <header className="fp-rise flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="text-sm text-muted-foreground">Your expense guide</p>
              <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
                {current?.period_start && current?.period_end
                  ? `${formatDate(current.period_start)} – ${formatDate(current.period_end)}`
                  : "Your money"}
              </h1>
              {current && (
                <p className="text-sm text-muted-foreground">
                  Built from {current.files} file{current.files === 1 ? "" : "s"}
                </p>
              )}
            </div>
            {jobs && <UploadSwitcher jobs={jobs} jobId={jobId} onOpen={open} />}
          </header>

          {jobError && (
            <Alert variant="destructive">
              <AlertCircle className="size-4" aria-hidden />
              <AlertTitle>Could not reach this upload</AlertTitle>
              <AlertDescription>{jobError}</AlertDescription>
            </Alert>
          )}

          {job && running && <Processing job={job} />}

          {job && job.status === "failed" && (
            <Card>
              <CardContent className="space-y-4">
                <Alert variant="destructive">
                  <AlertCircle className="size-4" aria-hidden />
                  <AlertTitle>This upload failed</AlertTitle>
                  <AlertDescription>{job.error}</AlertDescription>
                </Alert>
                <FileList files={job.files} onUnlock={setUnlocking} onMap={openMapping} />
              </CardContent>
            </Card>
          )}

          {job && finished && job.status !== "failed" && (
            <>
              {job.status === "needs_input" && (
                <Alert>
                  <AlertCircle className="size-4" aria-hidden />
                  <AlertTitle>Some files are waiting on you</AlertTitle>
                  <AlertDescription className="flex flex-wrap items-center gap-3">
                    A file needs a password or a column mapping. Everything else is ready.
                    <Button size="sm" variant="outline" onClick={() => setTab("files")}>Fix it</Button>
                  </AlertDescription>
                </Alert>
              )}

              <ExpenseLauncher jobId={job.job_id} />

              <RetentionCard
                job={job}
                onChanged={() => void refresh()}
                onDeleted={() => {
                  void refreshJobs();
                  open(null);
                }}
              />

              <nav className="sticky top-14 z-20 -mx-2 overflow-x-auto bg-background/80 px-2 py-2 backdrop-blur" aria-label="Dashboard sections">
                <div className="flex w-max gap-1 rounded-xl border bg-muted/50 p-1">
                  {TABS.map(({ id, label, icon: Icon }) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setTab(id)}
                      aria-current={tab === id ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm whitespace-nowrap transition-all",
                        tab === id ? "bg-background font-medium text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <Icon className={cn("size-4", tab === id && "text-primary")} aria-hidden />
                      {label}
                    </button>
                  ))}
                </div>
              </nav>

              {tab === "overview" && (
                <Overview jobId={job.job_id} onAsk={ask} onFindHabits={() => setTab("habits")} />
              )}
              {tab === "budget" && <BudgetPlanner key={job.job_id} jobId={job.job_id} />}
              {tab === "habits" && <HabitsView jobId={job.job_id} onAsk={ask} />}
              {tab === "transactions" && (
                <div className="space-y-6">
                  {obligations.length > 0 && <ObligationsCard obligations={obligations} />}
                  <PreviewTable jobId={job.job_id} onSelect={setSelected} selectedId={selected?.txn_id} />
                </div>
              )}
              <div hidden={tab !== "ask"}>
                <AgentChat jobId={job.job_id} ready={finished} seed={seed} />
              </div>
              <div hidden={tab !== "report"}>
                <ReportCard jobId={job.job_id} ready={finished} />
              </div>
              {tab === "files" && (
                <div className="space-y-6">
                  {summary && <SummaryCard summary={summary} jobId={job.job_id} />}
                  <Card>
                    <CardContent className="space-y-4">
                      <PipelineStepper job={job} />
                      <FileList files={job.files} onUnlock={setUnlocking} onMap={openMapping} />
                      <p className="text-xs text-muted-foreground">
                        Everything from this upload &mdash; files, ledger, habits, exports and reports &mdash; is
                        deleted {LEGAL.uploadRetentionDays} days after the analysis unless you extend it (Pro).
                      </p>
                    </CardContent>
                  </Card>
                </div>
              )}
            </>
          )}
        </>
      )}

      {jobId && (
        <>
          {/* Keyed so a different file remounts the dialog with fresh state,
              rather than an effect syncing state to a prop. */}
          {unlocking && (
            <PasswordDialog
              key={unlocking.doc_id}
              jobId={jobId}
              file={unlocking}
              onClose={() => setUnlocking(null)}
              onUnlocked={refresh}
            />
          )}
          {mappingFor && (
            <MappingDialog
              key={mappingFor.doc_id}
              jobId={jobId}
              mapping={mappingFor}
              onClose={() => setMappingFor(null)}
              onConfirmed={refresh}
            />
          )}
          {selected && (
            <TransactionPanel key={selected.txn_id} jobId={jobId} row={selected} onClose={() => setSelected(null)} />
          )}
        </>
      )}
    </div>
  );
}

function UploadSwitcher({
  jobs,
  jobId,
  onOpen,
}: {
  jobs: JobListItem[];
  jobId: string | null;
  onOpen: (id: string | null) => void;
}) {
  // read the clock once per mount, not on every render
  const [now] = useState(() => Date.now());
  const daysLeft = (iso: string) => Math.max(0, Math.ceil((new Date(iso).getTime() - now) / 86400000));
  return (
    <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
      {jobs.length > 0 && (
        <select
          value={jobId ?? ""}
          onChange={(event) => onOpen(event.target.value || null)}
          className="h-9 min-w-0 flex-1 rounded-lg border bg-background px-2 text-sm sm:max-w-64 sm:flex-none"
          aria-label="Your uploads"
        >
          {!jobId && <option value="">Your uploads…</option>}
          {jobs.map((j) => (
            <option key={j.job_id} value={j.job_id}>
              {j.period_start && j.period_end
                ? `${formatDate(j.period_start)} – ${formatDate(j.period_end)}`
                : formatDate(j.created_at?.slice(0, 10) ?? null)}
              {` · ${j.files} file${j.files === 1 ? "" : "s"}`}
              {j.expires_at ? ` · ${daysLeft(j.expires_at)}d left` : ""}
            </option>
          ))}
        </select>
      )}
      {jobId && (
        <Button variant="outline" size="sm" onClick={() => onOpen(null)}>
          <Plus className="size-4" aria-hidden />
          New upload
        </Button>
      )}
    </div>
  );
}
