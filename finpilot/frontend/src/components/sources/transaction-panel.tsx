"use client";



import { SourceBadge, sourceKindFor } from "@/components/sources/badges";
import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  formatDate,
  formatRupees,
  formatTime,
  type TransactionRow,
} from "@/lib/api";
import { useTransaction } from "@/components/sources/use-job";
import { cn } from "@/lib/utils";

const LINK_LABELS: Record<string, string> = {
  primary: "Primary source",
  email: "Order email / alert",
  receipt: "Receipt",
  spreadsheet: "Your spreadsheet",
  bill: "Bill",
};

/**
 * The side panel: everything known about one row, and where each part came
 * from. This is the answer to "why does the ledger say this?", which is the
 * question a user actually has when a number looks wrong.
 */
export function TransactionPanel({
  jobId,
  row,
  onClose,
}: {
  jobId: string;
  row: TransactionRow | null;
  onClose: () => void;
}) {
  const { detail, error } = useTransaction(jobId, row?.txn_id ?? null);

  if (!row) return null;
  const txn = detail?.transaction ?? row;

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle className="flex items-baseline justify-between gap-4">
            <span>{txn.merchant ?? "Transaction"}</span>
            <span
              className={cn(
                "tabular-nums",
                txn.direction === "credit" ? "text-emerald-600" : "",
              )}
            >
              {txn.direction === "credit" ? "+" : "−"}
              {formatRupees(txn.amount)}
            </span>
          </SheetTitle>
          <SheetDescription>
            {formatDate(txn.date)}
            {txn.time && ` at ${formatTime(txn.time)}`} · {txn.account}
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-6 px-4 pb-8">
          {txn.needs_review && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
              This row needs review — something did not reconcile or validate.
            </div>
          )}

          <Section title="As the statement put it">
            <p className="rounded-md bg-muted px-3 py-2 font-mono text-xs break-all">
              {txn.raw_description}
            </p>
          </Section>

          <Section title="Details">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <Field label="Payment mode" value={txn.payment_mode} />
              <Field label="Channel" value={txn.channel} />
              <Field label="Counterparty" value={txn.counterparty_type} />
              <Field label="Reference" value={txn.reference_no} mono />
              <Field label="Order id" value={txn.order_id} mono />
              <Field label="Card" value={txn.card_last4 && `•••• ${txn.card_last4}`} />
              <Field
                label="Balance after"
                value={txn.balance_after && formatRupees(txn.balance_after)}
              />
              <Field
                label="Confidence"
                value={`${Math.round(txn.extraction_confidence * 100)}% (${txn.extraction_method})`}
              />
            </dl>
            {txn.item_details && (
              <p className="mt-2 text-sm text-muted-foreground">{txn.item_details}</p>
            )}
          </Section>

          {detail?.items && detail.items.length > 0 && (
            <Section title="What was bought">
              <ul className="space-y-1 text-sm">
                {detail.items.map((item, index) => (
                  <li key={index} className="flex justify-between gap-4">
                    <span className="truncate">
                      {item.quantity && Number(item.quantity) !== 1 && (
                        <span className="text-muted-foreground">{item.quantity}× </span>
                      )}
                      {item.name}
                    </span>
                    <span className="shrink-0 tabular-nums">
                      {formatRupees(item.amount)}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <Section title="Where this came from">
            {!detail && !error && <Skeleton className="h-20 w-full" />}
            {error && <p className="text-sm text-destructive">{error}</p>}
            <ul className="space-y-2">
              {detail?.links.map((link) => (
                <li
                  key={`${link.link_type}:${link.source_ref}`}
                  className="flex items-start gap-3 rounded-md border px-3 py-2"
                >
                  <SourceBadge kind={sourceKindFor(link.source_ref)} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {link.filename ?? link.source_ref}
                    </p>
                    <p className="truncate font-mono text-xs text-muted-foreground">
                      {link.source_ref}
                    </p>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="text-xs text-muted-foreground">
                      {LINK_LABELS[link.link_type] ?? link.link_type}
                    </p>
                    {link.match_score < 1 && (
                      <Badge variant="outline" className="mt-0.5 font-normal">
                        {Math.round(link.match_score * 100)}% match
                      </Badge>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </Section>

          {detail?.raw_values && (
            <Section title="Raw values as extracted">
              <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-x-4 gap-y-1 text-xs">
                {Object.entries(detail.raw_values).map(([key, value]) => (
                  <div key={key} className="contents">
                    <dt className="truncate text-muted-foreground">{key}</dt>
                    <dd className="truncate font-mono break-all">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </Section>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">{title}</h3>
      <Separator />
      {children}
    </section>
  );
}

function Field({
  label, value, mono,
}: {
  label: string;
  value?: string | null;
  mono?: boolean;
}) {
  if (!value) return null;
  return (
    <div className="contents">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn("truncate", mono && "font-mono text-xs")} title={value}>
        {value}
      </dd>
    </div>
  );
}
