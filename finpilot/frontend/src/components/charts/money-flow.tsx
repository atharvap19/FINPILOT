"use client";

/**
 * Where the money came from and where it went: a three-column flow.
 *
 *   income sources ──▶ your money ──▶ spending groups + saved
 *
 * The middle column is honest about fungibility: the ledger cannot say which
 * rupee of salary paid for which order, so sources flow into one pool and the
 * pool flows out -- no invented source-to-target pairs. When spending exceeds
 * income, the gap enters on the left as "From your balance".
 *
 * Spend groups take the validated slots 1-5 in size order (the API folds the
 * rest into "Other", drawn grey); "Saved" wears the status-good colour with a
 * label, so it never relies on colour alone. Every node is direct-labelled.
 */

import { useMemo, useState } from "react";

import { ChartCard, DEEMPH, SLOTS, Tip, TipRow, inrCompact, inrFull, useTip } from "@/components/charts/viz";
import type { Insights } from "@/lib/api";

const W = 900;
const H = 340;
const NODE_W = 14;
const GAP = 10;
const COL = { left: 190, mid: W / 2 - NODE_W / 2, right: W - 190 - NODE_W };

interface Node {
  name: string;
  value: number;
  y0: number;
  y1: number;
  color: string;
  kind: "source" | "spend" | "saved" | "other" | "balance";
}

function stack(items: { name: string; value: number; kind: Node["kind"]; color: string }[], total: number): Node[] {
  const usable = H - GAP * Math.max(items.length - 1, 0) - 8;
  let y = 4;
  return items.map((item) => {
    const h = Math.max((item.value / total) * usable, 2);
    const node = { ...item, y0: y, y1: y + h };
    y += h + GAP;
    return node;
  });
}

/** A ribbon from a band on one column to a band on another. */
/** How much label a node has room for: two lines, one, or none (tooltip + table). */
function labelRoom(n: Node): "two" | "one" | "none" {
  const band = n.y1 - n.y0 + GAP;
  return band >= 34 ? "two" : band >= 16 ? "one" : "none";
}

const short = (name: string) => (name.length > 22 ? `${name.slice(0, 21)}…` : name);

function ribbon(x0: number, a0: number, a1: number, x1: number, b0: number, b1: number): string {
  const mx = (x0 + x1) / 2;
  return `M${x0},${a0} C${mx},${a0} ${mx},${b0} ${x1},${b0} L${x1},${b1} C${mx},${b1} ${mx},${a1} ${x0},${a1} Z`;
}

export function MoneyFlow({ data }: { data: Insights }) {
  const { ref, tip, show, hide } = useTip();
  const [hover, setHover] = useState<string | null>(null);
  const { flow } = data;

  const layout = useMemo(() => {
    const totalLeft = flow.sources.reduce((s, n) => s + n.value, 0);
    const totalRight = flow.targets.reduce((s, n) => s + n.value, 0);
    const total = Math.max(totalLeft, totalRight, 1);
    let slot = 0;
    const left = stack(
      flow.sources.map((s) => ({
        ...s,
        kind: s.name === "From your balance" ? ("balance" as const) : ("source" as const),
        color: s.name === "From your balance" ? "var(--viz-critical)" : "var(--viz-ink-2)",
      })),
      total,
    );
    const right = stack(
      flow.targets.map((t) => {
        if (t.kind === "saved") return { ...t, kind: "saved" as const, color: "var(--viz-good)" };
        if (t.name === "Other" || t.name === "Other spending" || t.name === "One-off spending") return { ...t, kind: "other" as const, color: DEEMPH };
        return { ...t, kind: "spend" as const, color: SLOTS[slot++] ?? DEEMPH };
      }),
      total,
    );
    const mid = { y0: 4, y1: H - 4 };
    const scale = (mid.y1 - mid.y0) / total;
    // where each ribbon meets the middle column, stacked in node order
    const bands = (nodes: Node[]) => {
      let at = mid.y0;
      return nodes.map((n) => {
        const b0 = at;
        at += n.value * scale;
        return { b0, b1: at };
      });
    };
    return { left, right, total, mid, leftBands: bands(left), rightBands: bands(right) };
  }, [flow]);

  if (!flow.sources.length && !flow.targets.length) return null;

  const tipFor = (node: Node) => (
    <>
      <TipRow color={node.color} value={inrFull(node.value)} label={node.name} />
      <p className="mt-0.5 text-muted-foreground">{((node.value / layout.total) * 100).toFixed(1)}% of the flow</p>
    </>
  );

  return (
    <ChartCard
      title="Where your money went"
      subtitle={`Income in, spending out — ${flow.grouped_by === "segment" ? "by spending habit" : "by merchant"}. Transfers between your own accounts are left out.`}
      table={{
        columns: ["Flow", "Amount", "Share"],
        rows: [
          ...layout.left.map((n) => [`In · ${n.name}`, inrFull(n.value), `${((n.value / layout.total) * 100).toFixed(1)}%`]),
          ...layout.right.map((n) => [`Out · ${n.name}`, inrFull(n.value), `${((n.value / layout.total) * 100).toFixed(1)}%`]),
        ],
      }}
    >
      <div ref={ref} className="relative overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H + 18}`} className="w-full min-w-[640px]" role="img" aria-label="Money flow from income to spending and savings">
          {/* left ribbons into the pool */}
          {layout.left.map((n, i) => {
            const { b0, b1 } = layout.leftBands[i];
            const active = hover === null || hover === n.name;
            return (
              <path
                key={`l-${n.name}`}
                d={ribbon(COL.left + NODE_W, n.y0, n.y1, COL.mid, b0, b1)}
                fill={n.color}
                fillOpacity={active ? 0.18 : 0.05}
                className="fp-fade transition-opacity"
                onPointerMove={(e) => { setHover(n.name); show(e, tipFor(n)); }}
                onPointerLeave={() => { setHover(null); hide(); }}
              />
            );
          })}
          {/* right ribbons out of the pool */}
          {layout.right.map((n, i) => {
            const { b0, b1 } = layout.rightBands[i];
            const active = hover === null || hover === n.name;
            return (
              <path
                key={`r-${n.name}`}
                d={ribbon(COL.mid + NODE_W, b0, b1, COL.right, n.y0, n.y1)}
                fill={n.color}
                fillOpacity={active ? 0.35 : 0.07}
                className="fp-fade transition-opacity"
                style={{ animationDelay: `${150 + i * 80}ms` }}
                onPointerMove={(e) => { setHover(n.name); show(e, tipFor(n)); }}
                onPointerLeave={() => { setHover(null); hide(); }}
              />
            );
          })}

          {/* nodes */}
          {layout.left.map((n) => (
            <rect key={`ln-${n.name}`} x={COL.left} y={n.y0} width={NODE_W} height={n.y1 - n.y0} rx={4} fill={n.color} />
          ))}
          <rect x={COL.mid} y={layout.mid.y0} width={NODE_W} height={layout.mid.y1 - layout.mid.y0} rx={4} fill="var(--viz-ink)" opacity={0.85} />
          {layout.right.map((n) => (
            <rect
              key={`rn-${n.name}`}
              x={COL.right}
              y={n.y0}
              width={NODE_W}
              height={n.y1 - n.y0}
              rx={4}
              fill={n.color}
              tabIndex={0}
              onFocus={(e) => { setHover(n.name); show(e, tipFor(n)); }}
              onBlur={() => { setHover(null); hide(); }}
            />
          ))}

          {/* labels: ink, never the series colour */}
          {layout.left.map((n) => {
            const room = labelRoom(n);
            if (room === "none") return null;
            return (
              <g key={`lt-${n.name}`} transform={`translate(${COL.left - 10},${(n.y0 + n.y1) / 2})`}>
                {room === "two" ? (
                  <>
                    <text textAnchor="end" dy={-3} fontSize={13} fill="var(--viz-ink-2)">{short(n.name)}</text>
                    <text textAnchor="end" dy={13} fontSize={13} fontWeight={600} fill="var(--viz-ink)">{inrCompact(n.value)}</text>
                  </>
                ) : (
                  <text textAnchor="end" dy={4} fontSize={12} fill="var(--viz-ink-2)">
                    {short(n.name)} <tspan fontWeight={600} fill="var(--viz-ink)">{inrCompact(n.value)}</tspan>
                  </text>
                )}
              </g>
            );
          })}
          <text x={COL.mid + NODE_W / 2} y={H + 14} textAnchor="middle" fontSize={11} fill="var(--viz-muted)">
            your money · {inrCompact(layout.total)}
          </text>
          {layout.right.map((n) => {
            const room = labelRoom(n);
            if (room === "none") return null;
            const pct = `${((n.value / layout.total) * 100).toFixed(0)}%`;
            return (
              <g key={`rt-${n.name}`} transform={`translate(${COL.right + NODE_W + 10},${(n.y0 + n.y1) / 2})`}>
                {room === "two" ? (
                  <>
                    <text dy={-3} fontSize={13} fill="var(--viz-ink-2)">{short(n.name)}</text>
                    <text dy={13} fontSize={13} fontWeight={600} fill="var(--viz-ink)">
                      {inrCompact(n.value)}
                      <tspan fontWeight={400} fill="var(--viz-muted)"> · {pct}</tspan>
                    </text>
                  </>
                ) : (
                  <text dy={4} fontSize={12} fill="var(--viz-ink-2)">
                    {short(n.name)} <tspan fontWeight={600} fill="var(--viz-ink)">{inrCompact(n.value)}</tspan>
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        <Tip tip={tip} />
      </div>
    </ChartCard>
  );
}
