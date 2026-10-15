"use client";

import { AlertTriangle, CheckCircle2, Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { api, formatRupees, type Summary } from "@/lib/api";
import { cn } from "@/lib/utils";

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone?: "warn" | "good";
}) {
  return (
    <div>
      <p
        className={cn(
          "text-2xl font-semibold tabular-nums",
          tone === "warn" && "text-amber-600",
          tone === "good" && "text-emerald-600",
        )}
      >
        {value}
      </p>
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  );
}

export function SummaryCard({
  summary,
  jobId,
}: {
  summary: Summary;
  jobId: string;
}) {
  const reconciled = summary.reconciliations;
  const allBalanced = reconciled.length > 0 && reconciled.every((r) => r.ok);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>What came out</CardTitle>
        <div className="flex flex-wrap gap-2">
          <Button asChild size="sm" variant="outline">
            <a href={api.exportUrl(jobId, "transactions.csv")} download title="Every transaction from every file, deduplicated, with segments">
              <Download className="size-3.5" aria-hidden />
              All transactions (CSV)
            </a>
          </Button>
          <Button asChild size="sm" variant="outline">
            <a href={api.exportUrl(jobId, "obligations.csv")} download title="Bills found in your uploads and whether they were paid">
              <Download className="size-3.5" aria-hidden />
              Bills (CSV)
            </a>
          </Button>
          <Button asChild size="sm">
            <a href={api.exportUrl(jobId, "finpilot_export.zip")} download title="Both CSVs, plus the financial report if you have generated one">
              <Download className="size-3.5" aria-hidden />
              Everything (.zip)
            </a>
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-5">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="files" value={summary.files_processed} />
          <Stat label="transactions" value={summary.rows} />
          <Stat label="duplicates merged" value={summary.duplicates_merged} />
          <Stat
            label="transfers found"
            value={summary.transfers_found + summary.card_payments_found}
          />
          <Stat
            label="emails & sheets linked"
            value={summary.emails_linked + summary.spreadsheet_rows_linked}
          />
          <Stat
            label="need review"
            value={summary.rows_needing_review}
            tone={summary.rows_needing_review > 0 ? "warn" : "good"}
          />
        </div>

        <Separator />

        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-muted-foreground">
          <span>{summary.refunds_found} refunds</span>
          <span>
            {summary.bills_found} bills ({summary.bills_paid} paid)
          </span>
          <span>{summary.links_made} source links</span>
          {summary.emails_added_as_rows > 0 && (
            <span>{summary.emails_added_as_rows} rows from emails alone</span>
          )}
        </div>

        {reconciled.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm font-medium">
              {allBalanced ? (
                <CheckCircle2 className="size-4 text-emerald-600" aria-hidden />
              ) : (
                <AlertTriangle className="size-4 text-amber-600" aria-hidden />
              )}
              Reconciliation
              <span className="font-normal text-muted-foreground">
                {allBalanced
                  ? "every statement balances to the paisa"
                  : "a statement does not balance"}
              </span>
            </div>
            <div className="overflow-hidden rounded-md border">
              <table className="w-full text-sm">
                <tbody>
                  {reconciled.map((row) => (
                    <tr key={row.filename} className="border-b last:border-0">
                      <td className="px-3 py-1.5">{row.filename}</td>
                      <td className="px-3 py-1.5 text-muted-foreground">{row.account}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {formatRupees(row.stated_closing)}
                      </td>
                      <td className="px-3 py-1.5 text-right">
                        {row.ok ? (
                          <span className="text-emerald-600">balanced</span>
                        ) : (
                          <span className="text-destructive">
                            off by {formatRupees(row.difference)}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
