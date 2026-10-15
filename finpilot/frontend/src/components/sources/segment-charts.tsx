"use client";

/**
 * The three segmentation charts.
 *
 * Hand-drawn SVG rather than a charting library: the project has no chart
 * dependency, and the mark specs here (a 2px surface gap between touching
 * fills, 4px rounded data-ends square at the baseline, an 8px dot with a 2px
 * surface ring) are the first thing a general-purpose library takes away.
 *
 * One colour assignment is shared by all three charts and is keyed on
 * `segment_id`, so a segment keeps its hue everywhere and filtering never
 * repaints the survivors. The five largest segments take the five validated
 * categorical slots; everything past that folds into a single grey "other"
 * rather than inventing a sixth hue.
 *
 * The scatter deliberately uses *emphasis* instead of colouring every segment
 * at once. A scatter puts all pairs of colours on screen simultaneously, and
 * the palette only clears the all-pairs colour-vision gates for three slots --
 * so the selected segment wears its hue and the rest recede to grey. Clicking
 * the legend moves the emphasis.
 */

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  formatRupees,
  type SegmentChartData,
  type SegmentSlice,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/** The validated categorical slots, in fixed order. Never cycled. */
const SLOTS = ["var(--viz-1)", "var(--viz-2)", "var(--viz-3)", "var(--viz-4)", "var(--viz-5)"];
const OTHER = "var(--viz-deemph)";

export interface SegmentColor {
  id: number;
  name: string;
  color: string;
}

/** Rank by money, hand out the five slots, fold the tail into one grey class.
 * Ranked by amount (not row count) so a habit wears the same colour here as in
 * the overview's money flow and the habit cards. */
export function assignColors(slices: SegmentSlice[]): Map<number, SegmentColor> {
  const ranked = [...slices].sort((a, b) => b.total_amount - a.total_amount);
  const out = new Map<number, SegmentColor>();
  ranked.forEach((slice, index) => {
    out.set(slice.segment_id, {
      id: slice.segment_id,
      name: slice.name,
      color: index < SLOTS.length ? SLOTS[index] : OTHER,
    });
  });
  return out;
}

function colorFor(colors: Map<number, SegmentColor>, id: number | null): string {
  if (id === null) return OTHER;
  return colors.get(id)?.color ?? OTHER;
}

// ---------------------------------------------------------------------------
// tooltip
// ---------------------------------------------------------------------------

interface Tip {
  x: number;
  y: number;
  lines: string[];
}

function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div
      className="pointer-events-none absolute z-10 rounded-md bg-popover px-2 py-1.5 text-xs text-popover-foreground shadow-md ring-1 ring-foreground/10"
      style={{ left: tip.x, top: tip.y, transform: "translate(-50%, -115%)" }}
      role="status"
    >
      {tip.lines.map((line, i) => (
        <div key={i} className={i === 0 ? "font-medium" : "text-muted-foreground"}>
          {line}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// pie
// ---------------------------------------------------------------------------

function polar(cx: number, cy: number, r: number, angle: number) {
  const rad = ((angle - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function arcPath(cx: number, cy: number, r: number, from: number, to: number) {
  const start = polar(cx, cy, r, to);
  const end = polar(cx, cy, r, from);
  const large = to - from <= 180 ? "0" : "1";
  return `M ${cx} ${cy} L ${start.x} ${start.y} A ${r} ${r} 0 ${large} 0 ${end.x} ${end.y} Z`;
}

interface Wedge {
  slice: SegmentSlice;
  from: number;
  to: number;
  share: number;
}

/** Running angles, as a pure function so nothing accumulates in render scope. */
function wedgesFor(slices: SegmentSlice[], total: number): Wedge[] {
  const out: Wedge[] = [];
  for (const slice of slices) {
    const from = out.length ? out[out.length - 1].to : 0;
    const sweep = (slice.size / total) * 360;
    out.push({ slice, from, to: from + sweep, share: slice.size / total });
  }
  return out;
}

function SegmentPie({
  slices,
  colors,
  onTip,
}: {
  slices: SegmentSlice[];
  colors: Map<number, SegmentColor>;
  onTip: (tip: Tip | null) => void;
}) {
  const total = slices.reduce((sum, s) => sum + s.size, 0);
  if (!total) return null;

  const size = 260;
  const cx = size / 2;
  const cy = size / 2;
  const r = 92;

  const wedges = wedgesFor(slices, total);

  return (
    <svg
      viewBox={`0 0 ${size} ${size}`}
      className="h-auto w-full max-w-[260px]"
      role="img"
      aria-label="Share of transactions by segment"
    >
      {wedges.map(({ slice, from, to, share }) => {
        const mid = (from + to) / 2;
        const label = polar(cx, cy, r * 0.66, mid);
        return (
          <g key={slice.segment_id}>
            <path
              d={arcPath(cx, cy, r, from, to)}
              fill={colorFor(colors, slice.segment_id)}
              /* the 2px gap is the surface showing through, not a border */
              stroke="var(--viz-surface)"
              strokeWidth={2}
              onMouseEnter={(e) =>
                onTip({
                  x: e.nativeEvent.offsetX,
                  y: e.nativeEvent.offsetY,
                  lines: [
                    slice.name,
                    `${slice.size} rows · ${(share * 100).toFixed(1)}%`,
                    formatRupees(slice.total_amount),
                  ],
                })
              }
              onMouseLeave={() => onTip(null)}
            />
            {/* Direct-label only the wedges with room. The rest are carried by
                the legend, the tooltip and the table view. */}
            {share >= 0.08 && (
              <text
                x={label.x}
                y={label.y}
                textAnchor="middle"
                dominantBaseline="middle"
                className="pointer-events-none text-[10px] font-medium"
                fill="#ffffff"
              >
                {(share * 100).toFixed(0)}%
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// stacked bar
// ---------------------------------------------------------------------------

function SegmentStack({
  data,
  colors,
  onTip,
}: {
  data: SegmentChartData;
  colors: Map<number, SegmentColor>;
  onTip: (tip: Tip | null) => void;
}) {
  const width = 560;
  const height = 240;
  const pad = { top: 12, right: 12, bottom: 34, left: 40 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const order = [...colors.keys()];
  const totals = data.months.map((m) =>
    Object.values(m.counts).reduce((a, b) => a + b, 0),
  );
  const max = Math.max(1, ...totals);
  const ticks = [0, Math.round(max / 2), max];

  const band = plotW / Math.max(1, data.months.length);
  const barW = Math.min(24, band * 0.62);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      role="img"
      aria-label="Transactions per month, split by segment"
    >
      {ticks.map((t) => {
        const y = pad.top + plotH - (t / max) * plotH;
        return (
          <g key={t}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={y}
              y2={y}
              stroke="var(--viz-grid)"
              strokeWidth={1}
            />
            <text
              x={pad.left - 8}
              y={y}
              textAnchor="end"
              dominantBaseline="middle"
              className="text-[10px] [font-variant-numeric:tabular-nums]"
              fill="var(--viz-muted)"
            >
              {t}
            </text>
          </g>
        );
      })}

      {data.months.map((month, i) => {
        const x = pad.left + i * band + (band - barW) / 2;
        let cursor = 0;
        const stack = order
          .filter((id) => (month.counts[String(id)] ?? 0) > 0)
          .map((id) => {
            const count = month.counts[String(id)] ?? 0;
            const h = (count / max) * plotH;
            const y = pad.top + plotH - cursor - h;
            cursor += h;
            return { id, count, y, h };
          });

        return (
          <g key={month.month}>
            {stack.map((part, index) => {
              const isTop = index === stack.length - 1;
              // 2px surface gap between touching segments; the topmost mark
              // gets the 4px rounded data-end, everything else stays square.
              const h = Math.max(1, part.h - (index === 0 ? 0 : 2));
              return (
                <rect
                  key={part.id}
                  x={x}
                  y={part.y}
                  width={barW}
                  height={h}
                  rx={isTop ? 4 : 0}
                  fill={colorFor(colors, part.id)}
                  onMouseEnter={(e) =>
                    onTip({
                      x: e.nativeEvent.offsetX,
                      y: e.nativeEvent.offsetY,
                      lines: [
                        colors.get(part.id)?.name ?? "other",
                        `${month.month} · ${part.count} rows`,
                      ],
                    })
                  }
                  onMouseLeave={() => onTip(null)}
                />
              );
            })}
          </g>
        );
      })}

      <line
        x1={pad.left}
        x2={width - pad.right}
        y1={pad.top + plotH}
        y2={pad.top + plotH}
        stroke="var(--viz-axis)"
        strokeWidth={1}
      />
      {data.months.map((month, i) => (
        <text
          key={month.month}
          x={pad.left + i * band + band / 2}
          y={height - 12}
          textAnchor="middle"
          className="text-[10px] [font-variant-numeric:tabular-nums]"
          fill="var(--viz-muted)"
        >
          {new Date(`${month.month}-01T00:00:00`).toLocaleDateString("en-IN", { month: "short", year: "2-digit" })}
        </text>
      ))}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// scatter
// ---------------------------------------------------------------------------

function SegmentScatter({
  data,
  colors,
  focus,
  onTip,
}: {
  data: SegmentChartData;
  colors: Map<number, SegmentColor>;
  focus: number | null;
  onTip: (tip: Tip | null) => void;
}) {
  const width = 560;
  const height = 240;
  const pad = { top: 12, right: 12, bottom: 34, left: 52 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const points = data.points.filter((p) => p.amount > 0);
  if (!points.length) return null;

  const times = points.map((p) => new Date(p.date).getTime());
  const minT = Math.min(...times);
  const maxT = Math.max(...times);
  // Amounts span three orders of magnitude, so a linear axis would pile every
  // everyday spend onto the baseline. The axis says so.
  const logs = points.map((p) => Math.log10(p.amount));
  const minL = Math.floor(Math.min(...logs));
  const maxL = Math.ceil(Math.max(...logs));

  const xOf = (t: number) =>
    pad.left + (maxT === minT ? plotW / 2 : ((t - minT) / (maxT - minT)) * plotW);
  const yOf = (a: number) =>
    pad.top + plotH - ((Math.log10(a) - minL) / Math.max(1e-9, maxL - minL)) * plotH;

  const decades = Array.from({ length: maxL - minL + 1 }, (_, i) => minL + i);
  // Draw the emphasised segment last so it sits above the grey field.
  const ordered = [...points].sort((a, b) => {
    const af = focus !== null && a.segment_id === focus ? 1 : 0;
    const bf = focus !== null && b.segment_id === focus ? 1 : 0;
    return af - bf;
  });

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      role="img"
      aria-label="Each transaction by date and amount, with the selected segment highlighted"
    >
      {decades.map((d) => {
        const y = yOf(Math.pow(10, d));
        return (
          <g key={d}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={y}
              y2={y}
              stroke="var(--viz-grid)"
              strokeWidth={1}
            />
            <text
              x={pad.left - 8}
              y={y}
              textAnchor="end"
              dominantBaseline="middle"
              className="text-[10px] [font-variant-numeric:tabular-nums]"
              fill="var(--viz-muted)"
            >
              {Math.pow(10, d).toLocaleString("en-IN")}
            </text>
          </g>
        );
      })}

      {ordered.map((point, i) => {
        const on = focus === null || point.segment_id === focus;
        const cx = xOf(new Date(point.date).getTime());
        const cy = yOf(point.amount);
        return (
          <g key={`${point.date}-${i}`}>
            <circle
              cx={cx}
              cy={cy}
              r={4}
              fill={on ? colorFor(colors, point.segment_id) : "var(--viz-deemph)"}
              opacity={on ? 1 : 0.35}
              stroke="var(--viz-surface)"
              strokeWidth={2}
            />
            {/* An invisible ~24px hit target: a 8px dot is too small to hover. */}
            <circle
              cx={cx}
              cy={cy}
              r={12}
              fill="transparent"
              onMouseEnter={(e) =>
                onTip({
                  x: e.nativeEvent.offsetX,
                  y: e.nativeEvent.offsetY,
                  lines: [
                    point.segment_id === null
                      ? "no segment"
                      : (colors.get(point.segment_id)?.name ?? "other"),
                    `${point.date} · ${formatRupees(point.amount)}`,
                  ],
                })
              }
              onMouseLeave={() => onTip(null)}
            />
          </g>
        );
      })}

      <line
        x1={pad.left}
        x2={width - pad.right}
        y1={pad.top + plotH}
        y2={pad.top + plotH}
        stroke="var(--viz-axis)"
        strokeWidth={1}
      />
      <text
        x={pad.left}
        y={height - 12}
        className="text-[10px]"
        fill="var(--viz-muted)"
      >
        {points[0]?.date}
      </text>
      <text
        x={width - pad.right}
        y={height - 12}
        textAnchor="end"
        className="text-[10px]"
        fill="var(--viz-muted)"
      >
        {points[points.length - 1]?.date}
      </text>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// the card
// ---------------------------------------------------------------------------

export function SegmentCharts({
  data,
  namedByModel,
}: {
  data: SegmentChartData;
  namedByModel: number;
}) {
  const [focus, setFocus] = useState<number | null>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const [showTable, setShowTable] = useState(false);

  const colors = useMemo(() => assignColors(data.slices), [data.slices]);
  const total = data.slices.reduce((sum, s) => sum + s.size, 0);

  if (!data.slices.length) return null;

  return (
    <Card className="viz-root">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Segments</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            {data.slices.length} patterns across {total} rows
            {data.unsegmented_rows > 0 &&
              ` · ${data.unsegmented_rows} one-offs left unsegmented`}
            {namedByModel === 0 && " · named without a model"}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowTable((v) => !v)}
          aria-pressed={showTable}
        >
          {showTable ? "Charts" : "Table"}
        </Button>
      </CardHeader>

      <CardContent className="relative">
        {/* Legend: always present, and the scatter's emphasis control. */}
        <div className="mb-4 flex flex-wrap gap-x-4 gap-y-2">
          {data.slices.map((slice) => {
            const color = colorFor(colors, slice.segment_id);
            const on = focus === null || focus === slice.segment_id;
            return (
              <button
                key={slice.segment_id}
                type="button"
                onClick={() =>
                  setFocus((f) => (f === slice.segment_id ? null : slice.segment_id))
                }
                aria-pressed={focus === slice.segment_id}
                className={cn(
                  "flex items-center gap-1.5 text-xs transition-opacity",
                  on ? "opacity-100" : "opacity-45",
                )}
              >
                <span
                  aria-hidden
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ background: color }}
                />
                <span className="text-foreground">{slice.name}</span>
                <span className="text-muted-foreground [font-variant-numeric:tabular-nums]">
                  {slice.size}
                </span>
              </button>
            );
          })}
        </div>

        {showTable ? (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-muted-foreground">
                <th className="py-1.5 text-left font-medium">Segment</th>
                <th className="py-1.5 text-right font-medium">Rows</th>
                <th className="py-1.5 text-right font-medium">Share</th>
                <th className="py-1.5 text-right font-medium">Total</th>
              </tr>
            </thead>
            <tbody>
              {data.slices.map((slice) => (
                <tr key={slice.segment_id} className="border-b last:border-0">
                  <td className="flex items-center gap-1.5 py-1.5">
                    <span
                      aria-hidden
                      className="size-2.5 shrink-0 rounded-full"
                      style={{ background: colorFor(colors, slice.segment_id) }}
                    />
                    {slice.name}
                  </td>
                  <td className="py-1.5 text-right [font-variant-numeric:tabular-nums]">
                    {slice.size}
                  </td>
                  <td className="py-1.5 text-right [font-variant-numeric:tabular-nums]">
                    {((slice.size / total) * 100).toFixed(1)}%
                  </td>
                  <td className="py-1.5 text-right [font-variant-numeric:tabular-nums]">
                    {formatRupees(slice.total_amount)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="grid gap-8 lg:grid-cols-[260px_1fr]">
            <figure className="m-0 flex flex-col items-center">
              <SegmentPie slices={data.slices} colors={colors} onTip={setTip} />
              <figcaption className="mt-2 text-center text-xs text-muted-foreground">
                Share of transactions
              </figcaption>
            </figure>

            <div className="flex flex-col gap-8">
              <figure className="m-0">
                <SegmentStack data={data} colors={colors} onTip={setTip} />
                <figcaption className="mt-1 text-xs text-muted-foreground">
                  Transactions per month, by segment
                </figcaption>
              </figure>

              <figure className="m-0">
                <SegmentScatter
                  data={data}
                  colors={colors}
                  focus={focus}
                  onTip={setTip}
                />
                <figcaption className="mt-1 text-xs text-muted-foreground">
                  Every transaction by date and amount (amount on a log scale)
                  {focus === null
                    ? " · select a segment above to highlight it"
                    : ` · highlighting ${colors.get(focus)?.name ?? ""}`}
                  {data.points_sampled && " · thinned for display"}
                </figcaption>
              </figure>
            </div>
          </div>
        )}
        <Tooltip tip={tip} />
      </CardContent>
    </Card>
  );
}
