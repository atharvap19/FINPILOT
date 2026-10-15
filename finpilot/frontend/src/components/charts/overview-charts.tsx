"use client";

/**
 * The overview's supporting charts. Each one answers one question:
 *
 * - CashFlow       did more come in than went out, month by month?
 * - SpendCalendar  which days does the money go? (sequential, one hue)
 * - WhenYouSpend   which weekdays (and, where times exist, which hours)?
 * - TopMerchants   who gets the most? (one series, one colour)
 * - SegmentTrends  is each habit growing or shrinking? (small multiples)
 */

import { useMemo } from "react";

import {
  ChartCard,
  SEQ,
  Tip,
  TipRow,
  barPath,
  columnPath,
  inrCompact,
  inrFull,
  monthLabel,
  niceTicks,
  useTip,
} from "@/components/charts/viz";
import type { Insights } from "@/lib/api";
import { cn } from "@/lib/utils";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// ---------------------------------------------------------------------------
// cash flow: two series, grouped columns, one axis
// ---------------------------------------------------------------------------

export function CashFlow({ data }: { data: Insights }) {
  const { ref, tip, show, hide } = useTip();
  const months = data.monthly;
  const W = 560;
  const H = 260;
  const pad = { top: 12, right: 8, bottom: 44, left: 48 };
  const max = Math.max(...months.flatMap((m) => [m.in, m.out]), 1);
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];
  const plotH = H - pad.top - pad.bottom;
  const band = (W - pad.left - pad.right) / Math.max(months.length, 1);
  const barW = Math.min(24, (band - 18) / 2);
  const y = (v: number) => pad.top + plotH - (v / top) * plotH;

  return (
    <ChartCard
      title="Money in vs spent"
      subtitle={`Each month, transfers between your own accounts excluded${months.some((m) => m.partial) ? " · * partial month" : ""}`}
      table={{
        columns: ["Month", "In", "Spent", "Net"],
        rows: months.map((m) => [monthLabel(m.month), inrFull(m.in), inrFull(m.out), inrFull(m.net)]),
      }}
    >
      <div className="mb-2 flex gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-[var(--viz-1)]" />Money in</span>
        <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-[var(--viz-2)]" />Spent</span>
      </div>
      <div ref={ref} className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Monthly money in and spending">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.left} x2={W - pad.right} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--viz-axis)" : "var(--viz-grid)"} strokeWidth={1} />
              <text x={pad.left - 8} y={y(t)} dy={4} textAnchor="end" fontSize={11} fill="var(--viz-muted)" className="tabular-nums">
                {inrCompact(t)}
              </text>
            </g>
          ))}
          {months.map((m, i) => {
            const cx = pad.left + band * i + band / 2;
            const tipBody = (
              <>
                <p className="mb-1 font-medium">{monthLabel(m.month)}{m.partial && " (partial month)"}</p>
                <TipRow color="var(--viz-1)" value={inrFull(m.in)} label="in" />
                <TipRow color="var(--viz-2)" value={inrFull(m.out)} label="spent" />
                <p className={cn("mt-1 font-medium", m.net >= 0 ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400")}>
                  {m.net >= 0 ? "Saved " : "Overspent "}{inrFull(Math.abs(m.net))}
                </p>
              </>
            );
            return (
              <g
                key={m.month}
                opacity={m.partial ? 0.45 : 1}
                onPointerMove={(e) => show(e, tipBody)}
                onPointerLeave={hide}
                tabIndex={0}
                onFocus={(e) => show(e, tipBody)}
                onBlur={hide}
                className="outline-none"
              >
                <rect x={cx - band / 2} y={pad.top} width={band} height={plotH} fill="transparent" className="hover:fill-[var(--viz-grid)]/40" />
                <path d={columnPath(cx - barW - 1, y(m.in), barW, y(0) - y(m.in))} fill="var(--viz-1)" className="fp-grow-y" style={{ animationDelay: `${i * 70}ms` }} />
                <path d={columnPath(cx + 1, y(m.out), barW, y(0) - y(m.out))} fill="var(--viz-2)" className="fp-grow-y" style={{ animationDelay: `${i * 70 + 40}ms` }} />
                <text x={cx} y={H - pad.bottom + 18} textAnchor="middle" fontSize={11} fill="var(--viz-ink-2)">
                  {monthLabel(m.month)}{m.partial ? "*" : ""}
                </text>
                <text x={cx} y={H - pad.bottom + 34} textAnchor="middle" fontSize={11} fontWeight={600} fill={m.net >= 0 ? "var(--viz-good-text)" : "var(--viz-critical)"}>
                  {m.net >= 0 ? "+" : "−"}{inrCompact(Math.abs(m.net))}
                </text>
              </g>
            );
          })}
        </svg>
        <Tip tip={tip} />
      </div>
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
// spending calendar: one cell per day, sequential blue
// ---------------------------------------------------------------------------

function quantileBins(values: number[]): number[] {
  const sorted = values.filter((v) => v > 0).sort((a, b) => a - b);
  if (!sorted.length) return [Infinity, Infinity, Infinity, Infinity];
  const q = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
  return [q(0.2), q(0.45), q(0.7), q(0.9)];
}

function binOf(value: number, cuts: number[]): number {
  if (value <= 0) return 0;
  let bin = 1;
  for (const cut of cuts) if (value > cut) bin += 1;
  return Math.min(bin, 5);
}

export function SpendCalendar({ data }: { data: Insights }) {
  const { ref, tip, show, hide } = useTip();
  const days = data.calendar;
  const cuts = useMemo(() => quantileBins(days.map((d) => d.out)), [days]);
  const first = new Date(`${days[0]?.date}T00:00:00`);
  const offset = (first.getDay() + 6) % 7; // Monday-first
  const weeks = Math.ceil((days.length + offset) / 7);
  const cell = 15;
  const gap = 3;
  const left = 30;
  const topPad = 18;
  const W = left + weeks * (cell + gap);
  const H = topPad + 7 * (cell + gap);
  const spendDays = days.filter((d) => d.out > 0);
  const biggest = spendDays.reduce((a, b) => (b.out > (a?.out ?? 0) ? b : a), spendDays[0]);
  const noSpend = days.length - spendDays.length;
  const fmtDay = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });

  const monthMarks: { x: number; label: string }[] = [];
  days.forEach((d, i) => {
    const date = new Date(`${d.date}T00:00:00`);
    if (date.getDate() === 1 || i === 0) {
      const week = Math.floor((i + offset) / 7);
      monthMarks.push({ x: left + week * (cell + gap), label: date.toLocaleDateString("en-IN", { month: "short" }) });
    }
  });

  return (
    <ChartCard
      title="Spending calendar"
      subtitle={
        <>
          {spendDays.length} days with spending · <span className="font-medium text-foreground">{noSpend} no-spend days</span>
          {biggest && <> · biggest day {fmtDay(biggest.date)} ({inrCompact(biggest.out)})</>}
        </>
      }
      table={{ columns: ["Date", "Spent"], rows: spendDays.map((d) => [fmtDay(d.date), inrFull(d.out)]) }}
    >
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
      <div>
      <div ref={ref} className="relative overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} style={{ minWidth: W, maxWidth: W * 1.5 }} className="w-full" role="img" aria-label="Daily spending calendar">
          {monthMarks.map((m) => (
            <text key={`${m.label}-${m.x}`} x={m.x} y={11} fontSize={10} fill="var(--viz-muted)">{m.label}</text>
          ))}
          {WEEKDAYS.map((d, i) => (i % 2 === 0 ? (
            <text key={d} x={0} y={topPad + i * (cell + gap) + cell - 3} fontSize={10} fill="var(--viz-muted)">{d}</text>
          ) : null))}
          {days.map((d, i) => {
            const slot = i + offset;
            const x = left + Math.floor(slot / 7) * (cell + gap);
            const yy = topPad + (slot % 7) * (cell + gap);
            const bin = binOf(d.out, cuts);
            const body = (
              <>
                <p className="font-medium">{fmtDay(d.date)}</p>
                <p className="text-muted-foreground">{d.out > 0 ? <><span className="font-semibold text-foreground">{inrFull(d.out)}</span> spent</> : "No spending"}</p>
              </>
            );
            return (
              <rect
                key={d.date}
                x={x}
                y={yy}
                width={cell}
                height={cell}
                rx={3}
                fill={SEQ[bin]}
                className="fp-fade cursor-default hover:brightness-90"
                style={{ animationDelay: `${Math.floor(slot / 7) * 25}ms` }}
                onPointerMove={(e) => show(e, body)}
                onPointerLeave={hide}
              />
            );
          })}
        </svg>
        <Tip tip={tip} />
      </div>
      <div className="mt-2 flex items-center justify-end gap-1.5 text-[11px] text-muted-foreground">
        Less
        {SEQ.map((c, i) => <span key={i} className="size-3 rounded-[3px]" style={{ background: c }} />)}
        More
      </div>
      </div>
      <TopDays days={spendDays} fmtDay={fmtDay} />
      </div>
    </ChartCard>
  );
}

function TopDays({ days, fmtDay }: { days: { date: string; out: number }[]; fmtDay: (iso: string) => string }) {
  const top = [...days].sort((a, b) => b.out - a.out).slice(0, 5);
  const average = days.length ? days.reduce((s, d) => s + d.out, 0) / days.length : 0;
  const max = top[0]?.out ?? 1;
  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-medium">Biggest spending days</p>
        <p className="text-xs text-muted-foreground">avg {inrCompact(average)} on a spending day</p>
      </div>
      <ol className="space-y-2.5">
        {top.map((d, i) => (
          <li key={d.date} className="flex items-center gap-3 text-sm">
            <span className="w-4 text-xs text-muted-foreground tabular-nums">{i + 1}</span>
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate">{fmtDay(d.date)}</span>
                <span className="font-semibold">{inrCompact(d.out)}</span>
              </div>
              <svg viewBox="0 0 100 6" preserveAspectRatio="none" className="mt-1 h-1.5 w-full" aria-hidden>
                <rect width={100} height={6} rx={3} fill="var(--viz-grid)" />
                <path d={barPath(0, 0, (d.out / max) * 100, 6, 3)} fill="var(--seq-4)" className="fp-grow-x" style={{ animationDelay: `${i * 70}ms` }} />
              </svg>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------------------
// when you spend
// ---------------------------------------------------------------------------

export function WhenYouSpend({ data }: { data: Insights }) {
  const { ref, tip, show, hide } = useTip();
  const { grid, weekday_totals: totals, timed_share } = data.week_hour;
  const useHours = timed_share >= 0.3;

  if (useHours) {
    const values = grid.flat();
    const cuts = quantileBins(values);
    const cell = 18;
    const gap = 2;
    const left = 34;
    const W = left + 24 * (cell + gap);
    const H = 16 + 7 * (cell + gap);
    return (
      <ChartCard
        title="When you spend"
        subtitle="Spending by weekday and hour of day"
        table={{
          columns: ["Day", ...Array.from({ length: 24 }, (_, h) => `${h}:00`)],
          rows: grid.map((row, d) => [WEEKDAYS[d], ...row.map((v) => (v ? inrCompact(v) : ""))]),
        }}
      >
        <div ref={ref} className="relative overflow-x-auto">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 420 }} role="img" aria-label="Spending heatmap by weekday and hour">
            {[0, 6, 12, 18, 23].map((h) => (
              <text key={h} x={left + h * (cell + gap)} y={10} fontSize={10} fill="var(--viz-muted)">{h === 0 ? "12a" : h === 12 ? "12p" : h < 12 ? `${h}a` : `${h - 12}p`}</text>
            ))}
            {grid.map((row, d) => (
              <g key={d}>
                <text x={0} y={16 + d * (cell + gap) + cell - 5} fontSize={10} fill="var(--viz-muted)">{WEEKDAYS[d]}</text>
                {row.map((v, h) => (
                  <rect
                    key={h}
                    x={left + h * (cell + gap)}
                    y={16 + d * (cell + gap)}
                    width={cell}
                    height={cell}
                    rx={3}
                    fill={SEQ[binOf(v, cuts)]}
                    onPointerMove={(e) => show(e, <TipRow value={v ? inrFull(v) : "—"} label={`${WEEKDAYS[d]} ${h}:00–${h + 1}:00`} />)}
                    onPointerLeave={hide}
                  />
                ))}
              </g>
            ))}
          </svg>
          <Tip tip={tip} />
        </div>
      </ChartCard>
    );
  }

  // Most bank statements carry dates but not times, so fall back to weekdays.
  const max = Math.max(...totals, 1);
  const peak = totals.indexOf(Math.max(...totals));
  const W = 420;
  const H = 210;
  const pad = { top: 22, bottom: 26, left: 8, right: 8 };
  const band = (W - pad.left - pad.right) / 7;
  const barW = Math.min(24 * 1.5, band - 14);
  const plotH = H - pad.top - pad.bottom;
  return (
    <ChartCard
      title="When you spend"
      subtitle={`Busiest day: ${WEEKDAYS[peak]} · by weekday (most statements don't record the time of day)`}
      table={{ columns: ["Day", "Spent"], rows: totals.map((v, d) => [WEEKDAYS[d], inrFull(v)]) }}
    >
      <div ref={ref} className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Spending by weekday">
          <line x1={pad.left} x2={W - pad.right} y1={pad.top + plotH} y2={pad.top + plotH} stroke="var(--viz-axis)" />
          {totals.map((v, d) => {
            const h = (v / max) * plotH;
            const x = pad.left + band * d + (band - barW) / 2;
            const yy = pad.top + plotH - h;
            return (
              <g key={d} onPointerMove={(e) => show(e, <TipRow value={inrFull(v)} label={WEEKDAYS[d]} />)} onPointerLeave={hide}>
                <rect x={pad.left + band * d} y={pad.top} width={band} height={plotH} fill="transparent" />
                <path d={columnPath(x, yy, barW, h)} fill={d === peak ? "var(--viz-1)" : "var(--viz-deemph)"} className="fp-grow-y" style={{ animationDelay: `${d * 60}ms` }} />
                {d === peak && (
                  <text x={x + barW / 2} y={yy - 6} textAnchor="middle" fontSize={11} fontWeight={600} fill="var(--viz-ink)">{inrCompact(v)}</text>
                )}
                <text x={x + barW / 2} y={H - 8} textAnchor="middle" fontSize={11} fill={d === peak ? "var(--viz-ink)" : "var(--viz-muted)"}>{WEEKDAYS[d]}</text>
              </g>
            );
          })}
        </svg>
        <Tip tip={tip} />
      </div>
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
// top merchants
// ---------------------------------------------------------------------------

export function TopMerchants({ data }: { data: Insights }) {
  const { ref, tip, show, hide } = useTip();
  const merchants = data.merchants;
  const max = Math.max(...merchants.map((m) => m.total), 1);
  return (
    <ChartCard
      title="Top merchants"
      subtitle="Who receives the most of your spending"
      table={{
        columns: ["Merchant", "Spent", "Payments", "Share"],
        rows: merchants.map((m) => [m.name, inrFull(m.total), m.count, `${(m.share * 100).toFixed(1)}%`]),
      }}
    >
      <div ref={ref} className="relative space-y-2.5">
        {merchants.map((m, i) => (
          <div
            key={m.name}
            className="group flex items-center gap-3"
            onPointerMove={(e) => show(e, <><TipRow color="var(--viz-1)" value={inrFull(m.total)} label={m.name} /><p className="text-muted-foreground">{m.count} payment{m.count === 1 ? "" : "s"} · {(m.share * 100).toFixed(1)}% of spending</p></>)}
            onPointerLeave={hide}
          >
            <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
              {m.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase()}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-2 text-sm">
                <span className="truncate">{m.name}</span>
                <span className="shrink-0 font-semibold">{inrCompact(m.total)}</span>
              </div>
              <svg viewBox="0 0 100 6" preserveAspectRatio="none" className="mt-1 h-1.5 w-full" aria-hidden>
                <rect x={0} y={0} width={100} height={6} rx={3} fill="var(--viz-grid)" />
                <path d={barPath(0, 0, (m.total / max) * 100, 6, 3)} fill="var(--viz-1)" className="fp-grow-x" style={{ animationDelay: `${i * 60}ms` }} />
              </svg>
            </div>
          </div>
        ))}
        <Tip tip={tip} />
      </div>
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
// segment trends: small multiples, one series each -> one hue, no legend
// ---------------------------------------------------------------------------

export function SegmentTrends({ data }: { data: Insights }) {
  const months = data.period.trend_months;
  const trends = data.segment_trends.slice(0, 6);
  if (!trends.length) return null;
  const W = 220;
  const H = 70;
  return (
    <ChartCard
      title="How each habit is trending"
      subtitle="Monthly spending per segment, on each panel's own scale"
      table={{
        columns: ["Segment", ...months.map(monthLabel), "Total"],
        rows: trends.map((t) => [t.name, ...t.series.map((v) => inrFull(v)), inrFull(t.total)]),
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {trends.map((t) => {
          const max = Math.max(...t.series, 1);
          const x = (i: number) => (i / Math.max(t.series.length - 1, 1)) * (W - 10) + 5;
          const y = (v: number) => H - 6 - (v / max) * (H - 14);
          const line = t.series.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
          const area = `${line} L${x(t.series.length - 1)},${H - 6} L${x(0)},${H - 6} Z`;
          const change = t.change;
          return (
            <div key={t.name} className="rounded-lg border p-3">
              <div className="flex items-baseline justify-between gap-2">
                <p className="truncate text-sm font-medium" title={t.name}>{t.name}</p>
                <p className="shrink-0 text-sm font-semibold">{inrCompact(t.total)}</p>
              </div>
              <svg viewBox={`0 0 ${W} ${H}`} className="mt-1 w-full" role="img" aria-label={`${t.name} monthly trend`}>
                <path d={area} fill="var(--viz-1)" opacity={0.1} />
                <path d={line} fill="none" stroke="var(--viz-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" className="fp-draw" style={{ ["--fp-len" as string]: 500 }} />
                {t.series.map((v, i) => (
                  <circle key={i} cx={x(i)} cy={y(v)} r={i === t.series.length - 1 ? 4 : 0} fill="var(--viz-1)" stroke="var(--viz-surface)" strokeWidth={2}>
                    <title>{`${monthLabel(months[i])}: ${inrFull(v)}`}</title>
                  </circle>
                ))}
              </svg>
              <p className="text-xs text-muted-foreground">
                {months.length ? `${monthLabel(months[0])} – ${monthLabel(months[months.length - 1])}` : ""}
                {change !== null && Number.isFinite(change) && (
                  <span className={cn("ml-1 font-medium", change > 0 ? "text-red-700 dark:text-red-400" : "text-emerald-700 dark:text-emerald-400")}>
                    {change > 0 ? "▲" : "▼"} {Math.abs(change * 100).toFixed(0)}%
                  </span>
                )}
              </p>
            </div>
          );
        })}
      </div>
    </ChartCard>
  );
}
