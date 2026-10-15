"use client";

/**
 * A chart the agent placed in an answer or a report, by tag:
 *
 *   [[chart:segment_split]]   [[chart:segment:3]]   [[chart:monthly]] ...
 *
 * The agent only names the chart; the figures come from the same insights the
 * dashboard draws (and the segmentation behind them), so an embedded chart is
 * the dashboard's chart -- live, hoverable, with its table view -- and can
 * never disagree with the data. Unknown or undrawable tags render nothing.
 */

import { useInsights } from "@/components/dashboard/overview";
import { HabitCard, HabitDonut, colorAt } from "@/components/dashboard/habits";
import { KpiTiles } from "@/components/charts/kpi-tiles";
import { MoneyFlow } from "@/components/charts/money-flow";
import { CashFlow, SegmentTrends, SpendCalendar, TopMerchants, WhenYouSpend } from "@/components/charts/overview-charts";
import { SegmentsCard } from "@/components/sources/segments-card";
import { Skeleton } from "@/components/ui/skeleton";

export const CHART_TAG = /\[\[chart:([a-z_]+)(?::(-?\d+))?\]\]/g;

export function ChartEmbed({ jobId, kind, arg }: { jobId: string; kind: string; arg?: string }) {
  const { data } = useInsights(jobId);
  if (!data) return <Skeleton className="my-3 h-48 w-full rounded-xl" />;
  if (data.empty) return null;

  const body = (() => {
    switch (kind) {
      case "segment_split":
        return data.segmented ? <HabitDonut data={data} /> : null;
      case "segment_trends":
        return data.segmented ? <SegmentTrends data={data} /> : null;
      case "segment": {
        const index = data.segment_trends.findIndex((h) => String(h.segment_id) === arg);
        if (index < 0) return null;
        return (
          <div className="max-w-md">
            <HabitCard habit={data.segment_trends[index]} color={colorAt(index)} months={data.period.trend_months} index={0} />
          </div>
        );
      }
      case "segments_detail":
        return data.segmented ? <SegmentsCard jobId={jobId} ready /> : null;
      case "money_flow":
        return <MoneyFlow data={data} />;
      case "monthly":
        return <CashFlow data={data} />;
      case "merchants":
        return <TopMerchants data={data} />;
      case "weekday":
        return <WhenYouSpend data={data} />;
      case "calendar":
        return <SpendCalendar data={data} />;
      case "kpis":
        return <KpiTiles data={data} />;
      default:
        return null;
    }
  })();

  // wide-format charts may use the full width; the rest stay chart-sized
  const wide = kind === "money_flow" || kind === "kpis" || kind === "segments_detail";
  return body ? <div className={wide ? "my-4" : "my-4 max-w-3xl"}>{body}</div> : null;
}
