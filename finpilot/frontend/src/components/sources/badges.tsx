"use client";

import {
  AlertTriangle,
  Banknote,
  CheckCircle2,
  CreditCard,
  FileSpreadsheet,
  FileText,
  Loader2,
  Lock,
  Mail,
  MinusCircle,
  Receipt,
  Table2,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { FileState } from "@/lib/api";

/**
 * The source a row or a file came from, as an icon plus a label.
 *
 * Every row in the ledger carries its provenance, so the table shows it: a
 * transaction confirmed by a bank statement *and* an order email is a
 * different kind of fact from one seen only in a spreadsheet.
 */

const SOURCE_STYLES = {
  bank_statement: { icon: Banknote, label: "Bank", className: "bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950 dark:text-sky-300 dark:border-sky-900" },
  cc_statement: { icon: CreditCard, label: "Card", className: "bg-violet-50 text-violet-700 border-violet-200 dark:bg-violet-950 dark:text-violet-300 dark:border-violet-900" },
  email: { icon: Mail, label: "Email", className: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900" },
  receipt: { icon: Receipt, label: "Receipt", className: "bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-950 dark:text-rose-300 dark:border-rose-900" },
  spreadsheet: { icon: FileSpreadsheet, label: "Sheet", className: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-900" },
  bill: { icon: FileText, label: "Bill", className: "bg-orange-50 text-orange-700 border-orange-200 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-900" },
} as const;

type SourceKind = keyof typeof SOURCE_STYLES;

/** Work out the source kind from a source_ref like `emails/order.eml#1`. */
export function sourceKindFor(ref: string): SourceKind {
  const name = ref.split("#")[0].toLowerCase();
  if (name.endsWith(".eml") || name.endsWith(".mbox")) return "email";
  if (name.endsWith(".xlsx") || name.endsWith(".xls")) return "spreadsheet";
  if (name.includes("bill") || name.includes("invoice")) return "bill";
  if (name.endsWith(".pdf")) return "cc_statement";
  return "bank_statement";
}

export function SourceBadge({
  kind,
  title,
  className,
}: {
  kind: SourceKind | string;
  title?: string;
  className?: string;
}) {
  const style = SOURCE_STYLES[kind as SourceKind] ?? SOURCE_STYLES.bank_statement;
  const Icon = style.icon;
  return (
    <Badge
      variant="outline"
      title={title}
      className={cn("gap-1 font-normal", style.className, className)}
    >
      <Icon className="size-3" aria-hidden />
      {style.label}
    </Badge>
  );
}

/** One badge per distinct source that contributed to a row. */
export function SourceBadges({ linkedSources }: { linkedSources: string }) {
  const refs = linkedSources.split("|").filter(Boolean);
  const kinds = Array.from(new Set(refs.map(sourceKindFor)));
  if (!kinds.length) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {kinds.map((kind) => (
        <SourceBadge
          key={kind}
          kind={kind}
          title={refs.filter((r) => sourceKindFor(r) === kind).join("\n")}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// file status
// ---------------------------------------------------------------------------

const FILE_STATES: Record<
  FileState,
  { label: string; icon: typeof CheckCircle2; className: string; spin?: boolean }
> = {
  queued: { label: "Queued", icon: Loader2, className: "text-muted-foreground" },
  processing: { label: "Processing", icon: Loader2, className: "text-sky-600", spin: true },
  needs_password: { label: "Needs password", icon: Lock, className: "text-amber-600" },
  needs_mapping: { label: "Needs mapping", icon: Table2, className: "text-amber-600" },
  processed: { label: "Done", icon: CheckCircle2, className: "text-emerald-600" },
  needs_review: { label: "Needs review", icon: AlertTriangle, className: "text-amber-600" },
  no_transactions: { label: "No transactions", icon: MinusCircle, className: "text-muted-foreground" },
  failed: { label: "Failed", icon: XCircle, className: "text-destructive" },
  duplicate: { label: "Duplicate", icon: CheckCircle2, className: "text-muted-foreground" },
};

export function FileStatusBadge({ status }: { status: FileState }) {
  const state = FILE_STATES[status] ?? FILE_STATES.queued;
  const Icon = state.icon;
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm", state.className)}>
      <Icon className={cn("size-4", state.spin && "animate-spin")} aria-hidden />
      {state.label}
    </span>
  );
}

const DOCUMENT_ICONS: Record<string, typeof FileText> = {
  bank_statement: Banknote,
  cc_statement: CreditCard,
  email: Mail,
  spreadsheet: FileSpreadsheet,
  bill: FileText,
  receipt: Receipt,
};

export function DocumentIcon({ type, className }: { type: string; className?: string }) {
  const Icon = DOCUMENT_ICONS[type] ?? FileText;
  return <Icon className={cn("size-4 text-muted-foreground", className)} aria-hidden />;
}

export function MethodBadge({ method }: { method: string | null }) {
  if (!method) return null;
  const labels: Record<string, string> = {
    template: "template",
    pdfplumber: "pdf text",
    spreadsheet: "spreadsheet",
    email: "email",
  };
  return (
    <Badge
      variant="outline"
      className="font-mono text-[10px] font-normal"
      title="How this file was read"
    >
      {labels[method] ?? method}
    </Badge>
  );
}
