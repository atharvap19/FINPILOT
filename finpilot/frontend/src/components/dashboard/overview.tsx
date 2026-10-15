"use client";

/**
 * The Overview tab: the four headline numbers, then the charts that explain
 * them. Everything here comes from GET /insights, which is computed by code --
 * loading the dashboard never spends a model token.
 */

import { ArrowRight, ChartPie, MessageCircleQuestion } from "lucide-react";
import useSWR from "swr";

import { KpiTiles } from "@/components/charts/kpi-tiles";
import { MoneyFlow } from "@/components/charts/money-flow";
import { CashFlow, SegmentTrends, SpendCalendar, TopMerchants, WhenYouSpend } from "@/components/charts/overview-charts";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Insights } from "@/lib/api";

export function useInsights(jobId: string) {
  return useSWR<Insights>(["insights", jobId], () => api.insights(jobId), { revalidateOnFocus: false });
}

function suggestions(data: Insights): string[] {
  const out: string[] = [];
  const months = data.monthly.filter((m) => !m.partial);
  if (months.length >= 2) {
    const name = (key: string) => new Date(`${key}-01T00:00:00`).toLocaleDateString("en-IN", { month: "long" });
    out.push(`Why did my spending change from ${name(months[0].month)} to ${name(months[months.length - 1].month)}?`);
  }
  if (data.merchants[0]) out.push(`How much do I spend at ${data.merchants[0].name}, and is it worth cutting?`);
  if (data.kpis.bills_due.count) out.push("Which bills are still unpaid and when are they due?");
  out.push("Where could I realistically save ₹5,000 a month?");
  return out.slice(0, 4);
}

export function Overview({
  jobId,
  onAsk,
  onFindHabits,
}: {
  jobId: string;
  onAsk: (question: string) => void;
  onFindHabits: () => void;
}) {
  const { data, error, isLoading } = useInsights(jobId);

  if (isLoading && !data) {
    return (
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-36 rounded-xl" />)}
        </div>
        <Skeleton className="h-96 rounded-xl" />
      </div>
    );
  }
  if (error || !data) return <p className="text-sm text-destructive">{error?.message ?? "Could not load the overview."}</p>;
  if (data.empty) {
    return <Card className="p-10 text-center text-sm text-muted-foreground">No transactions came out of these files yet.</Card>;
  }

  return (
    <div className="space-y-6">
      <KpiTiles data={data} />

      <Card className="fp-rise flex-row flex-wrap items-center gap-2 border-dashed bg-gradient-to-r from-indigo-500/5 via-transparent to-fuchsia-500/5 p-4" style={{ animationDelay: "200ms" }}>
        <span className="mr-1 flex items-center gap-1.5 text-sm font-medium">
          <MessageCircleQuestion className="size-4 text-primary" />
          Ask FinPilot
        </span>
        {suggestions(data).map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onAsk(q)}
            className="rounded-full border bg-background px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
          >
            {q}
          </button>
        ))}
      </Card>

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="min-w-0 xl:col-span-2"><MoneyFlow data={data} /></div>
        <TopMerchants data={data} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <CashFlow data={data} />
        <WhenYouSpend data={data} />
      </div>

      <SpendCalendar data={data} />

      {data.segmented ? (
        <SegmentTrends data={data} />
      ) : (
        <Card className="flex-row flex-wrap items-center justify-between gap-4 p-6">
          <div className="flex items-center gap-3">
            <span className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary"><ChartPie className="size-5" /></span>
            <div>
              <p className="font-medium">Discover your spending habits</p>
              <p className="text-sm text-muted-foreground">Group your transactions into the habits they really are — late-night orders, weekend outings, fixed bills.</p>
            </div>
          </div>
          <Button onClick={onFindHabits}>Find my habits <ArrowRight className="size-4" /></Button>
        </Card>
      )}
    </div>
  );
}
