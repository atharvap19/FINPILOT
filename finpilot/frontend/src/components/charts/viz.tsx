"use client";

/**
 * Shared pieces for every dashboard chart.
 *
 * Charts are hand-drawn SVG (see segment-charts.tsx for why): thin marks, 4px
 * rounded data-ends, a 2px surface gap between touching fills, hairline
 * recessive grids, text in ink tokens -- never in the series colour. Every
 * chart sits in a ChartCard, which carries a table view so no value is only
 * reachable by hovering.
 */

import { BarChart3, Table2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** Validated categorical slots, fixed order, never cycled. */
export const SLOTS = ["var(--viz-1)", "var(--viz-2)", "var(--viz-3)", "var(--viz-4)", "var(--viz-5)"];
export const DEEMPH = "var(--viz-deemph)";
export const SEQ = ["var(--seq-0)", "var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"];

/** ₹ in Indian units: ₹950, ₹12.4K, ₹1.25L, ₹3.1Cr. */
export function inrCompact(value: number): string {
  const sign = value < 0 ? "-" : "";
  const v = Math.abs(value);
  const fmt = (n: number, unit: string) =>
    `${sign}₹${n >= 100 ? Math.round(n) : n.toFixed(n >= 10 ? 1 : 2).replace(/\.?0+$/, "")}${unit}`;
  if (v >= 1e7) return fmt(v / 1e7, "Cr");
  if (v >= 1e5) return fmt(v / 1e5, "L");
  if (v >= 1e3) return fmt(v / 1e3, "K");
  return `${sign}₹${Math.round(v)}`;
}

export function inrFull(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

export function monthLabel(key: string): string {
  const [y, m] = key.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "short", year: "2-digit" });
}

/** "Nice" axis ticks from 0 to just above max. */
export function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0];
  const raw = max / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw;
  const ticks: number[] = [];
  for (let t = 0; t <= max + step * 0.001; t += step) ticks.push(t);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

/** Rounded-top bar path: 4px data-end, square at the baseline. */
export function columnPath(x: number, y: number, w: number, h: number, r = 4): string {
  if (h <= 0) return "";
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`;
}

/** Rounded-end horizontal bar: square at the left baseline. */
export function barPath(x: number, y: number, w: number, h: number, r = 4): string {
  if (w <= 0) return "";
  const rr = Math.min(r, h / 2, w);
  return `M${x},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h - rr} Q${x + w},${y + h} ${x + w - rr},${y + h} H${x} Z`;
}

// ---------------------------------------------------------------------------
// tooltip
// ---------------------------------------------------------------------------

export interface TipState {
  x: number;
  y: number;
  content: ReactNode;
}

/** Tracks a tooltip inside a `relative` container. */
export function useTip() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [tip, setTip] = useState<TipState | null>(null);
  const show = (event: React.PointerEvent | React.FocusEvent, content: ReactNode) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    if ("clientX" in event) {
      setTip({ x: event.clientX - box.left, y: event.clientY - box.top, content });
    } else {
      const target = (event.target as Element).getBoundingClientRect();
      setTip({ x: target.left - box.left + target.width / 2, y: target.top - box.top, content });
    }
  };
  return { ref, tip, show, hide: () => setTip(null) };
}

export function Tip({ tip }: { tip: TipState | null }) {
  if (!tip) return null;
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-20 min-w-36 -translate-x-1/2 -translate-y-[calc(100%+12px)] rounded-lg border bg-popover px-3 py-2 text-xs shadow-lg"
      style={{ left: tip.x, top: tip.y }}
    >
      {tip.content}
    </div>
  );
}

/** Tooltip row: a short line key in the series colour, value first. */
export function TipRow({ color, label, value }: { color?: string; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 py-0.5">
      {color && <span className="h-0.5 w-3 rounded-full" style={{ background: color }} />}
      <span className="font-semibold text-foreground">{value}</span>
      <span className="text-muted-foreground">{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// card with a table twin
// ---------------------------------------------------------------------------

export interface TableView {
  columns: string[];
  rows: (string | number)[][];
}

export function ChartCard({
  title,
  subtitle,
  table,
  children,
  className,
  action,
}: {
  title: string;
  subtitle?: ReactNode;
  table?: TableView;
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    // min-w-0: a chart that scrolls sideways inside its card must not widen
    // the grid column (and with it the whole page) on a phone
    <Card className={cn("viz-root min-w-0 gap-3 overflow-hidden", className)}>
      <div className="flex items-start justify-between gap-3 px-6">
        <div className="min-w-0">
          <h3 className="text-base leading-tight font-semibold">{title}</h3>
          {subtitle && <p className="mt-1 text-xs text-muted-foreground">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {action}
          {table && (
            <button
              type="button"
              onClick={() => setAsTable((v) => !v)}
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              title={asTable ? "Show chart" : "Show as table"}
              aria-pressed={asTable}
            >
              {asTable ? <BarChart3 className="size-4" aria-hidden /> : <Table2 className="size-4" aria-hidden />}
              <span className="sr-only">{asTable ? "Show chart" : "Show as table"}</span>
            </button>
          )}
        </div>
      </div>
      <CardContent>
        {asTable && table ? (
          <div className="max-h-80 overflow-auto rounded-md border">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-muted">
                <tr>
                  {table.columns.map((c, i) => (
                    <th key={c} className={cn("px-3 py-1.5 font-medium", i === 0 ? "text-left" : "text-right")}>
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, r) => (
                  <tr key={r} className="border-t">
                    {row.map((cell, i) => (
                      <td key={i} className={cn("px-3 py-1.5", i === 0 ? "text-left" : "text-right tabular-nums")}>
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// motion
// ---------------------------------------------------------------------------

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Animate a number from 0 to `target` once, on mount. */
export function useCountUp(target: number, duration = 900): number {
  const [value, setValue] = useState(0);
  const [reduced] = useState(prefersReducedMotion);
  useEffect(() => {
    if (reduced) return;
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - (1 - t) ** 3;
      setValue(target * eased);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, duration, reduced]);
  return reduced ? target : value;
}
