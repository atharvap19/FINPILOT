"use client";

/**
 * The Spending habits tab.
 *
 * Not segmented yet -> the invitation to run it.
 * Segmented        -> a donut of how spending splits across habits, one story
 *                     card per habit (cost, trend, where, when, why), then the
 *                     detailed transaction-level charts.
 *
 * Colour follows the habit, ranked by spend: the same five validated slots as
 * the overview's money flow, everything past them (and one-off spending) grey.
 */

import { CalendarDays, Clock, MessageCircleQuestion, Store, TrendingDown, TrendingUp } from "lucide-react";
import { useState } from "react";

import { useInsights } from "@/components/dashboard/overview";
import { ChartCard, DEEMPH, SLOTS, Tip, TipRow, inrCompact, inrFull, monthLabel, useTip } from "@/components/charts/viz";
import { SegmentsCard } from "@/components/sources/segments-card";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Habit, Insights } from "@/lib/api";
import { cn } from "@/lib/utils";

export const colorAt = (i: number) => (i < SLOTS.length ? SLOTS[i] : DEEMPH);

// ---------------------------------------------------------------------------
// donut
// ---------------------------------------------------------------------------

function polar(cx: number, cy: number, r: number, a: number) {
  return [cx + r * Math.cos(a - Math.PI / 2), cy + r * Math.sin(a - Math.PI / 2)];
}

function arc(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const [x0, y0] = polar(cx, cy, r1, a0);
  const [x1, y1] = polar(cx, cy, r1, a1);
  const [x2, y2] = polar(cx, cy, r0, a1);
  const [x3, y3] = polar(cx, cy, r0, a0);
  return `M${x0},${y0} A${r1},${r1} 0 ${large} 1 ${x1},${y1} L${x2},${y2} A${r0},${r0} 0 ${large} 0 ${x3},${y3} Z`;
}

export function HabitDonut({ data }: { data: Insights }) {
  const { ref, tip, show, hide } = useTip();
  const [active, setActive] = useState<string | null>(null);
  const total = data.kpis.money_out.value;
  const habits = data.segment_trends;
  const named = habits.slice(0, SLOTS.length);
  const rest = total - named.reduce((s, h) => s + h.total, 0);
  const slices = [
    ...named.map((h, i) => ({ name: h.name, value: h.total, color: colorAt(i) })),
    ...(rest > 0.5 ? [{ name: "One-offs & other", value: rest, color: DEEMPH }] : []),
  ];
  const explained = named.reduce((s, h) => s + h.total, 0) / (total || 1);
  const size = 220;
  const c = size / 2;
  let angle = 0;

  return (
    <ChartCard
      title="How your spending splits"
      subtitle={`${habits.length} habit${habits.length === 1 ? "" : "s"} explain ${(explained * 100).toFixed(0)}% of what you spent`}
      table={{ columns: ["Habit", "Spent", "Share"], rows: slices.map((s) => [s.name, inrFull(s.value), `${((s.value / total) * 100).toFixed(1)}%`]) }}
    >
      <div className="flex flex-col items-center gap-6 sm:flex-row">
        <div ref={ref} className="relative shrink-0">
          <svg viewBox={`0 0 ${size} ${size}`} className="size-52" role="img" aria-label="Spending split by habit">
            {slices.map((s) => {
              const sweep = (s.value / total) * Math.PI * 2;
              const a0 = angle;
              angle += sweep;
              const on = active === null || active === s.name;
              return (
                <path
                  key={s.name}
                  d={arc(c, c, 66, active === s.name ? 104 : 100, a0, a0 + sweep)}
                  fill={s.color}
                  stroke="var(--viz-surface)"
                  strokeWidth={2}
                  fillOpacity={on ? 1 : 0.35}
                  className="transition-all duration-200"
                  onPointerMove={(e) => { setActive(s.name); show(e, <TipRow color={s.color} value={inrFull(s.value)} label={s.name} />); }}
                  onPointerLeave={() => { setActive(null); hide(); }}
                />
              );
            })}
            <text x={c} y={c - 4} textAnchor="middle" fontSize={22} fontWeight={600} fill="var(--viz-ink)">{inrCompact(total)}</text>
            <text x={c} y={c + 16} textAnchor="middle" fontSize={11} fill="var(--viz-muted)">spent</text>
          </svg>
          <Tip tip={tip} />
        </div>
        <ul className="w-full space-y-2">
          {slices.map((s) => (
            <li
              key={s.name}
              className={cn("rounded-lg px-2 py-1 transition-colors", active === s.name && "bg-muted")}
              onPointerEnter={() => setActive(s.name)}
              onPointerLeave={() => setActive(null)}
            >
              <div className="flex items-center gap-2 text-sm">
                <span className="size-2.5 shrink-0 rounded-sm" style={{ background: s.color }} />
                <span className="min-w-0 flex-1 truncate">{s.name}</span>
                <span className="font-semibold">{inrCompact(s.value)}</span>
                <span className="w-10 text-right text-xs text-muted-foreground tabular-nums">{((s.value / total) * 100).toFixed(0)}%</span>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
// habit story cards
// ---------------------------------------------------------------------------

function hourLabel(h: number): string {
  if (h === 0) return "midnight";
  if (h === 12) return "noon";
  return h < 12 ? `${h} am` : `${h - 12} pm`;
}

export function HabitCard({
  habit,
  color,
  months,
  index,
  onAsk,
}: {
  habit: Habit;
  color: string;
  months: string[];
  index: number;
  /** omitted when the card is embedded in an answer */
  onAsk?: (q: string) => void;
}) {
  const W = 260;
  const H = 64;
  const max = Math.max(...habit.series, 1);
  const x = (i: number) => (i / Math.max(habit.series.length - 1, 1)) * (W - 10) + 5;
  const y = (v: number) => H - 5 - (v / max) * (H - 12);
  const line = habit.series.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  const area = `${line} L${x(habit.series.length - 1)},${H - 5} L${x(0)},${H - 5} Z`;
  const up = habit.change !== null && habit.change > 0;

  return (
    <Card className="fp-rise viz-root gap-3 overflow-hidden p-0" style={{ animationDelay: `${index * 70}ms` }}>
      <div className="h-1" style={{ background: color }} />
      <div className="space-y-3 px-5 pb-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate font-semibold first-letter:uppercase" title={habit.name}>{habit.name}</p>
            <p className="text-xs text-muted-foreground">
              {habit.count} payments · typically {inrCompact(habit.median)}
            </p>
          </div>
          <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs font-medium">
            {(habit.share * 100).toFixed(0)}% of spending
          </span>
        </div>

        <div className="flex items-end justify-between gap-3">
          <div>
            <p className="text-2xl font-semibold tracking-tight">{inrCompact(habit.total)}</p>
            <p className="text-xs text-muted-foreground">≈ {inrCompact(habit.per_month)} a month</p>
          </div>
          {habit.change !== null && Number.isFinite(habit.change) && (
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
                up ? "bg-red-500/10 text-red-700 dark:text-red-400" : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
              )}
            >
              {up ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />}
              {Math.abs(habit.change * 100).toFixed(0)}% {up ? "up" : "down"}
            </span>
          )}
        </div>

        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`${habit.name} by month`}>
          <path d={area} fill={color} fillOpacity={0.12} />
          <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" className="fp-draw" style={{ ["--fp-len" as string]: 500 }} />
          {habit.series.map((v, i) => (
            <circle key={i} cx={x(i)} cy={y(v)} r={4} fill={color} stroke="var(--viz-surface)" strokeWidth={2}>
              <title>{`${monthLabel(months[i])}: ${inrFull(v)}`}</title>
            </circle>
          ))}
        </svg>
        <div className="flex justify-between text-[11px] text-muted-foreground">
          {months.map((m) => <span key={m}>{monthLabel(m)}</span>)}
        </div>

        <ul className="space-y-1.5 text-sm text-muted-foreground">
          {habit.merchants.length > 0 && (
            <li className="flex items-center gap-2">
              <Store className="size-3.5 shrink-0" />
              <span className="truncate">Mostly at <span className="text-foreground">{habit.merchants.map((m) => m.name).join(", ")}</span></span>
            </li>
          )}
          {habit.busiest_day && (
            <li className="flex items-center gap-2">
              <CalendarDays className="size-3.5 shrink-0" />
              Busiest on <span className="text-foreground">{habit.busiest_day}</span>
            </li>
          )}
          {habit.busiest_hour !== null && (
            <li className="flex items-center gap-2">
              <Clock className="size-3.5 shrink-0" />
              Usually around <span className="text-foreground">{hourLabel(habit.busiest_hour)}</span>
            </li>
          )}
        </ul>

        {onAsk && <button
          type="button"
          onClick={() => onAsk(`Tell me about my "${habit.name}" spending habit — what drives it, and how could I spend less on it?`)}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border py-2 text-sm font-medium transition-colors hover:bg-muted"
        >
          <MessageCircleQuestion className="size-4 text-primary" />
          Ask FinPilot about this habit
        </button>}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// the tab
// ---------------------------------------------------------------------------

export function HabitsView({ jobId, onAsk }: { jobId: string; onAsk: (q: string) => void }) {
  const { data, isLoading, mutate } = useInsights(jobId);

  if (isLoading && !data) return <Skeleton className="h-96 rounded-xl" />;

  if (!data?.segmented) {
    return <SegmentsCard jobId={jobId} ready onSegmented={() => void mutate()} />;
  }

  const months = data.period.trend_months;
  return (
    <div className="space-y-6">
      <HabitDonut data={data} />
      <div>
        <h2 className="mb-3 text-lg font-semibold tracking-tight">Your habits, one by one</h2>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.segment_trends.map((habit, i) => (
            <HabitCard key={habit.segment_id} habit={habit} color={colorAt(i)} months={months} index={i} onAsk={onAsk} />
          ))}
        </div>
      </div>
      <div>
        <h2 className="mb-3 text-lg font-semibold tracking-tight">Every transaction, by habit</h2>
        <SegmentsCard jobId={jobId} ready onSegmented={() => void mutate()} />
      </div>
    </div>
  );
}
