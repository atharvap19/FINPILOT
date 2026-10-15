"use client";

/**
 * Today's (or this week's) budget.
 *
 *   left to spend = budget - what you plan - fixed payments due - what you usually spend
 *
 * The user types a budget and their plans; FinPilot adds what they may have
 * forgotten -- rent or subscriptions falling due in the window, and the
 * spending that usually happens on days like this -- all learned from their
 * ledger by the API, with the basis of every line shown. Only a day or a
 * week: further out, one-off expenses swamp any estimate.
 */

import { CalendarDays, Info, Loader2, PiggyBank } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { inrFull } from "@/components/charts/viz";
import { LEDGER_CHANGED } from "@/components/expenses/expense-chat";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api, type BudgetLine, type BudgetPlan } from "@/lib/api";
import { cn } from "@/lib/utils";

type Period = "day" | "week";

const PARTS = [
  { key: "spent", label: "Already spent", color: "var(--viz-4)" },
  { key: "planned", label: "You plan", color: "var(--viz-1)" },
  { key: "commitments", label: "Fixed payments due", color: "var(--viz-2)" },
  { key: "usual", label: "Usual spends", color: "var(--viz-3)" },
] as const;

function todayIso(): string {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function load(jobId: string, period: Period): { budget: string; text: string } {
  try {
    const raw = window.localStorage.getItem(`finpilot.budget.${jobId}.${period}`);
    if (raw) return JSON.parse(raw);
  } catch {
    /* storage unavailable */
  }
  return { budget: period === "day" ? "1500" : "8000", text: "" };
}

function save(jobId: string, period: Period, value: { budget: string; text: string }) {
  try {
    window.localStorage.setItem(`finpilot.budget.${jobId}.${period}`, JSON.stringify(value));
  } catch {
    /* storage unavailable */
  }
}

export function BudgetPlanner({ jobId }: { jobId: string }) {
  const [period, setPeriod] = useState<Period>("day");
  const [inputs, setInputs] = useState(() => load(jobId, "day"));
  const [excluded, setExcluded] = useState<string[]>([]);
  const [plan, setPlan] = useState<BudgetPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [start] = useState(todayIso);
  // typed expenses elsewhere on the page change what's left: recalculate
  const [ledgerVersion, setLedgerVersion] = useState(0);
  useEffect(() => {
    const bump = () => setLedgerVersion((v) => v + 1);
    window.addEventListener(LEDGER_CHANGED, bump);
    return () => window.removeEventListener(LEDGER_CHANGED, bump);
  }, []);

  const switchPeriod = (next: Period) => {
    setPeriod(next);
    setInputs(load(jobId, next));
    setExcluded([]);
  };

  // recalculate as the user types (debounced); nothing here calls a model
  useEffect(() => {
    const budget = Number(inputs.budget.replace(/[,₹\s]/g, ""));
    save(jobId, period, inputs);
    if (!Number.isFinite(budget) || budget < 0) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setBusy(true);
      try {
        const result = await api.budget(jobId, { period, budget, text: inputs.text, start, exclude: excluded });
        if (!controller.signal.aborted) {
          setPlan(result);
          setError(null);
        }
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof ApiError ? err.message : String(err));
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    }, 350);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [jobId, period, inputs, excluded, start, ledgerVersion]);

  const toggle = (line: BudgetLine) =>
    setExcluded((prev) => (prev.includes(line.key) ? prev.filter((k) => k !== line.key) : [...prev, line.key]));

  const range = useMemo(() => {
    if (!plan) return "";
    const fmt = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
    return plan.days === 1 ? fmt(plan.start) : `${fmt(plan.start)} – ${fmt(plan.end)}`;
  }, [plan]);

  return (
    <div className="grid gap-6 lg:grid-cols-5">
      {/* inputs */}
      <Card className="gap-5 p-5 lg:col-span-2">
        <div className="flex items-center gap-2">
          <span className="flex size-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <PiggyBank className="size-5" aria-hidden />
          </span>
          <div>
            <p className="font-semibold">Plan your spending</p>
            <p className="text-xs text-muted-foreground">We add what you might forget, from your own history.</p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted p-1" role="tablist">
          {(["day", "week"] as const).map((p) => (
            <button
              key={p}
              type="button"
              role="tab"
              aria-selected={period === p}
              onClick={() => switchPeriod(p)}
              className={cn(
                "rounded-md py-1.5 text-sm transition-colors",
                period === p ? "bg-background font-medium shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {p === "day" ? "Today" : "This week"}
            </button>
          ))}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="budget-amount">Your budget for {period === "day" ? "today" : "the next 7 days"}</Label>
          <div className="relative">
            <span className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground">₹</span>
            <Input
              id="budget-amount"
              inputMode="decimal"
              className="pl-7 text-lg font-semibold"
              value={inputs.budget}
              onChange={(e) => setInputs({ ...inputs, budget: e.target.value })}
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="budget-plan">What will you spend on?</Label>
          <textarea
            id="budget-plan"
            rows={4}
            value={inputs.text}
            onChange={(e) => setInputs({ ...inputs, text: e.target.value })}
            placeholder={period === "day" ? "lunch 200, petrol 500, movie 350" : "groceries 1,500\npetrol 800\ndinner out 1,200"}
            className="w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
          />
          <p className="text-xs text-muted-foreground">One per line or separated by commas — amount before or after.</p>
          {plan && plan.planned.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {plan.planned.map((item, i) => (
                <span key={`${item.label}-${i}`} className="rounded-full border bg-muted/40 px-2 py-0.5 text-xs">
                  {item.label} <b>{inrFull(item.amount)}</b>
                </span>
              ))}
            </div>
          )}
        </div>

        <p className="flex gap-2 rounded-lg bg-muted/40 p-3 text-xs text-muted-foreground">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          We plan a day or a week at most. Further ahead, one-off expenses — a repair, a wedding, a hospital bill —
          are too unpredictable for any figure to be honest.
        </p>
      </Card>

      {/* result */}
      <div className="space-y-4 lg:col-span-3">
        {error && <p className="text-sm text-destructive">{error}</p>}
        {!plan && !error && (
          <Card className="items-center justify-center p-10 text-sm text-muted-foreground">
            <Loader2 className="size-5 animate-spin" aria-hidden />
          </Card>
        )}
        {plan && <Result plan={plan} range={range} busy={busy} onToggle={toggle} />}
      </div>
    </div>
  );
}

function Result({
  plan,
  range,
  busy,
  onToggle,
}: {
  plan: BudgetPlan;
  range: string;
  busy: boolean;
  onToggle: (line: BudgetLine) => void;
}) {
  const over = plan.safe_to_spend < 0;
  const amounts = {
    spent: plan.spent_total,
    planned: plan.planned_total,
    commitments: plan.commitments_total,
    usual: plan.usual_total,
  };
  const used = amounts.spent + amounts.planned + amounts.commitments + amounts.usual;
  const scale = Math.max(plan.budget, used, 1);
  const left = Math.max(plan.safe_to_spend, 0);

  return (
    <div className={cn("space-y-4 transition-opacity", busy && "opacity-70")}>
      <Card className="viz-root gap-4 p-6">
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <CalendarDays className="size-4" aria-hidden /> {range}
        </p>
        <div>
          <p className={cn("text-4xl font-semibold tracking-tight", over ? "text-red-600 dark:text-red-400" : plan.status === "tight" ? "text-amber-600 dark:text-amber-400" : "text-emerald-600 dark:text-emerald-400")}>
            {over ? `${inrFull(-plan.safe_to_spend)} over` : inrFull(plan.safe_to_spend)}
          </p>
          <p className="text-sm text-muted-foreground">
            {over
              ? `Your plans and usual spending add up to more than your ${plan.days === 1 ? "day's" : "week's"} budget.`
              : plan.days === 1
                ? "left to spend today, after your plans and what you usually spend"
                : `left for the week — about ${inrFull(plan.per_day)} a day`}
          </p>
        </div>

        {/* the split, one bar, every part labelled */}
        <div>
          <div className="flex h-3 w-full overflow-hidden rounded-full bg-[var(--viz-grid)]" aria-hidden>
            {PARTS.map((part) =>
              amounts[part.key] > 0 ? (
                <span key={part.key} className="h-full border-r-2 border-[var(--viz-surface)]" style={{ width: `${(amounts[part.key] / scale) * 100}%`, background: part.color }} />
              ) : null,
            )}
            {left > 0 && <span className="h-full" style={{ width: `${(left / scale) * 100}%`, background: "var(--viz-good)" }} />}
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-5">
            <div>
              <dt className="text-xs text-muted-foreground">Budget</dt>
              <dd className="font-semibold">{inrFull(plan.budget)}</dd>
            </div>
            {PARTS.map((part) => (
              <div key={part.key} className={cn(part.key === "spent" && amounts.spent === 0 && "hidden sm:block")}>
                <dt className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span className="size-2 rounded-sm" style={{ background: part.color }} />
                  {part.label}
                </dt>
                <dd className="font-semibold">− {inrFull(amounts[part.key])}</dd>
              </div>
            ))}
          </dl>
        </div>
      </Card>

      {plan.commitments.length > 0 && (
        <Lines title="Fixed payments due" hint="Payments you make around the same date every month." lines={plan.commitments} onToggle={onToggle} />
      )}
      <Lines
        title={plan.days === 1 ? "What you usually spend on a day like this" : "What you usually spend in a week"}
        hint="Learned from your history. Untick anything that won’t happen this time."
        lines={plan.usual}
        onToggle={onToggle}
        empty="Nothing regular stands out — your history has no steady spends for this period."
      />
      {plan.history && (
        <p className="text-xs text-muted-foreground">
          Based on {plan.history.weeks} weeks of your transactions ({plan.history.from} to {plan.history.to}).
          Calculated by FinPilot from your ledger — not AI-generated.
        </p>
      )}
    </div>
  );
}

function Lines({
  title,
  hint,
  lines,
  onToggle,
  empty,
}: {
  title: string;
  hint: string;
  lines: BudgetLine[];
  onToggle: (line: BudgetLine) => void;
  empty?: string;
}) {
  return (
    <Card className="gap-3 p-5">
      <div>
        <p className="font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      {lines.length === 0 && empty && <p className="text-sm text-muted-foreground">{empty}</p>}
      <ul className="divide-y">
        {lines.map((line) => (
          <li key={line.key} className={cn("flex items-start gap-3 py-2.5", !line.included && "opacity-60")}>
            <Checkbox
              checked={line.included}
              disabled={line.covered_by !== null}
              onCheckedChange={() => onToggle(line)}
              className="mt-0.5"
              aria-label={`Count ${line.label}`}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-3">
                <p className="truncate text-sm font-medium">
                  {line.label}
                  {line.date && <span className="ml-1.5 text-xs font-normal text-muted-foreground">due {new Date(`${line.date}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</span>}
                </p>
                <p className={cn("shrink-0 text-sm font-semibold", !line.included && "line-through")}>{inrFull(line.amount)}</p>
              </div>
              <p className="text-xs text-muted-foreground">
                {line.covered_by ? `Covered by your “${line.covered_by}” — not counted twice. ` : ""}
                {line.basis}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
