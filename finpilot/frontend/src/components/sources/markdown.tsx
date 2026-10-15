"use client";

/**
 * Markdown as the agent writes it: headings, lists, bold figures, GFM tables
 * and inline citations. Styled by hand rather than with a typography plugin so
 * tables and code spans match the rest of the page.
 */

import { Fragment } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CHART_TAG, ChartEmbed } from "@/components/charts/chart-embed";
import { cn } from "@/lib/utils";

const components: Components = {
  h1: ({ children }) => (
    <h1 className="mb-3 mt-1 text-xl font-semibold tracking-tight">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-2 mt-6 border-b pb-1 text-base font-semibold">{children}</h2>
  ),
  h3: ({ children }) => <h3 className="mb-1.5 mt-4 text-sm font-semibold">{children}</h3>,
  h4: ({ children }) => <h4 className="mb-1 mt-3 text-sm font-medium">{children}</h4>,
  p: ({ children }) => <p className="my-2 leading-relaxed">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  strong: ({ children }) => (
    <strong className="font-semibold text-foreground">{children}</strong>
  ),
  em: ({ children }) => <em className="text-muted-foreground">{children}</em>,
  hr: () => <hr className="my-4 border-border" />,
  code: ({ children }) => (
    <code className="rounded bg-muted px-1 py-0.5 text-[0.85em] font-medium">{children}</code>
  ),
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer" className="underline underline-offset-2">
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-md border">
      <table className="w-full text-xs">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-muted/60">{children}</thead>,
  th: ({ children }) => (
    <th className="px-2.5 py-1.5 text-left font-medium whitespace-nowrap">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border-t px-2.5 py-1.5 tabular-nums">{children}</td>
  ),
};

type Piece = { kind: "text"; text: string } | { kind: "chart"; chart: string; arg?: string };

/** Split agent text into markdown runs and `[[chart:...]]` tags. */
function pieces(text: string): Piece[] {
  const out: Piece[] = [];
  let last = 0;
  for (const match of text.matchAll(CHART_TAG)) {
    const at = match.index ?? 0;
    if (at > last) out.push({ kind: "text", text: text.slice(last, at) });
    out.push({ kind: "chart", chart: match[1], arg: match[2] });
    last = at + match[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

/**
 * Agent-written markdown. With a `jobId`, chart tags the agent placed become
 * live charts drawn from that job's data; without one they are dropped.
 */
export function Markdown({ text, className, jobId }: { text: string; className?: string; jobId?: string }) {
  return (
    <div className={cn("text-sm text-foreground/90", className)}>
      {pieces(text).map((piece, i) =>
        piece.kind === "chart" ? (
          jobId ? <ChartEmbed key={i} jobId={jobId} kind={piece.chart} arg={piece.arg} /> : <Fragment key={i} />
        ) : piece.text.trim() ? (
          <ReactMarkdown key={i} remarkPlugins={[remarkGfm]} components={components}>
            {piece.text}
          </ReactMarkdown>
        ) : null,
      )}
    </div>
  );
}
