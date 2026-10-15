"use client";

/**
 * The four numbers the dashboard leads with. Each is a stat tile: label,
 * value (counting up on first paint), a signed change against the previous
 * full month coloured by whether that direction is good, and a sparkline of
 * the monthly series in the de-emphasis hue with the latest month in accent.
 */

import { ArrowDownRight, ArrowUpRight, CalendarClock, CheckCircle2, PiggyBank, TrendingDown, Wallet } from "lucide-react";
import type { ReactNode } from "react";

import { inrCompact, inrFull, monthLabel, useCountUp } from "@/components/charts/viz";
import { Card } from "@/components/ui/card";
import type { Insights } from "@/lib/api";
import { cn } from "@/lib/utils";

function Sparkline({ series, months }: { series: number[]; months: string[] }) {
  if (series.length < 2) return null;
  const w = 120;
  const h = 34;
  const min = Math.min(0, ...series);
  const max = Math.max(...series, 1);
  const x = (i: number) => (i / (series.length - 1)) * (w - 8) + 4;
  const y = (v: number) => h - 4 - ((v - min) / (max - min || 1)) * (h - 8);
  const d = series.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = series.length - 1;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-9 w-28" aria-hidden>
      <title>{months.map((m, i) => `${monthLabel(m)} ${inrFull(series[i])}`).join(", ")}</title>
      {min < 0 && <line x1={0} x2={w} y1={y(0)} y2={y(0)} stroke="var(--viz-grid)" strokeWidth={1} />}
      <path d={d} fill="none" stroke="var(--viz-deemph)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="fp-draw" style={{ ["--fp-len" as string]: 300 }} />
      <circle cx={x(last)} cy={y(series[last])} r={4} fill="var(--viz-1)" stroke="var(--viz-surface)" strokeWidth={2} />
    </svg>
  );
}

function Delta({ value, upIsGood }: { value: number | null | undefined; upIsGood: boolean }) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  const up = value >= 0;
  const good = up === upIsGood;
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-xs font-medium",
        good ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "bg-red-500/10 text-red-700 dark:text-red-400",
      )}
    >
      <Icon className="size-3" aria-hidden />
      {Math.abs(value * 100).toFixed(0)}%
      <span className="sr-only">{up ? "up" : "down"} vs last month</span>
    </span>
  );
}

function Tile({
  label,
  icon,
  value,
  format = inrCompact,
  footer,
  spark,
  delay,
  tone,
}: {
  label: string;
  icon: ReactNode;
  value: number;
  format?: (v: number) => string;
  footer: ReactNode;
  spark?: ReactNode;
  delay: number;
  tone: string;
}) {
  const shown = useCountUp(value);
  return (
    <Card className="fp-rise viz-root relative gap-2 overflow-hidden p-5" style={{ animationDelay: `${delay}ms` }}>
      <div className={cn("pointer-events-none absolute -top-10 -right-10 size-28 rounded-full opacity-15 blur-2xl", tone)} />
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <span className={cn("flex size-7 items-center justify-center rounded-lg text-white", tone)}>{icon}</span>
        {label}
      </div>
      <div className="flex items-end justify-between gap-2">
        <p className="text-3xl font-semibold tracking-tight" title={inrFull(value)}>
          {format(shown)}
        </p>
        {spark}
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">{footer}</div>
    </Card>
  );
}

export function KpiTiles({ data }: { data: Insights }) {
  const { kpis, period } = data;
  const months = period.trend_months;
  const rate = kpis.saved.rate;
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Tile
        label="Money in"
        icon={<Wallet className="size-4" aria-hidden />}
        tone="bg-sky-600"
        value={kpis.money_in.value}
        delay={0}
        spark={<Sparkline series={kpis.money_in.series ?? []} months={months} />}
        footer={
          <>
            <Delta value={kpis.money_in.delta} upIsGood /> vs last month
          </>
        }
      />
      <Tile
        label="Spent"
        icon={<TrendingDown className="size-4" aria-hidden />}
        tone="bg-orange-600"
        value={kpis.money_out.value}
        delay={80}
        spark={<Sparkline series={kpis.money_out.series ?? []} months={months} />}
        footer={
          <>
            <Delta value={kpis.money_out.delta} upIsGood={false} /> vs last month · transfers excluded
          </>
        }
      />
      <Tile
        label={kpis.saved.value >= 0 ? "Saved" : "Overspent"}
        icon={<PiggyBank className="size-4" aria-hidden />}
        tone={kpis.saved.value >= 0 ? "bg-emerald-600" : "bg-red-600"}
        value={kpis.saved.value}
        delay={160}
        spark={<Sparkline series={kpis.saved.series ?? []} months={months} />}
        footer={
          rate !== null && rate !== undefined && Number.isFinite(rate) && Math.abs(rate) <= 5 ? (
            <>
              <span className="font-medium text-foreground">{(rate * 100).toFixed(0)}%</span> of income
              {rate >= 0.2 ? " — healthy" : rate >= 0 ? " — room to improve" : " — spending exceeds income"}
            </>
          ) : (
            "no income in these files to compare against"
          )
        }
      />
      <Tile
        label="Bills due"
        icon={kpis.bills_due.count ? <CalendarClock className="size-4" aria-hidden /> : <CheckCircle2 className="size-4" aria-hidden />}
        tone={kpis.bills_due.count ? "bg-amber-600" : "bg-emerald-600"}
        value={kpis.bills_due.value}
        delay={240}
        footer={
          kpis.bills_due.total_bills === 0
            ? "no bills uploaded"
            : kpis.bills_due.count
              ? `${kpis.bills_due.count} of ${kpis.bills_due.total_bills} bills still unpaid`
              : `all ${kpis.bills_due.total_bills} bills paid`
        }
      />
    </div>
  );
}
