"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate, formatRupees, type Obligation } from "@/lib/api";

const STATUS_STYLES: Record<Obligation["status"], string> = {
  paid: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-900",
  overdue: "bg-red-50 text-red-700 border-red-200 dark:bg-red-950 dark:text-red-300 dark:border-red-900",
  due: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-900",
};

/** Bills found in the uploads, and whether a payment was found for each. */
export function ObligationsCard({ obligations }: { obligations: Obligation[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Bills</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50 text-left">
                <th className="px-3 py-2 font-medium">Biller</th>
                <th className="px-3 py-2 font-medium">Period</th>
                <th className="px-3 py-2 font-medium">Due</th>
                <th className="px-3 py-2 text-right font-medium">Amount</th>
                <th className="px-3 py-2 text-right font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {obligations.map((bill) => (
                <tr key={bill.bill_id} className="border-b last:border-0">
                  <td className="px-3 py-2">
                    <div className="font-medium">{bill.biller}</div>
                    {bill.consumer_no && (
                      <div className="font-mono text-xs text-muted-foreground">
                        {bill.consumer_no}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {bill.billing_period ?? "—"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                    {formatDate(bill.due_date)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                    {formatRupees(bill.amount_due)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Badge variant="outline" className={STATUS_STYLES[bill.status]}>
                      {bill.status}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
