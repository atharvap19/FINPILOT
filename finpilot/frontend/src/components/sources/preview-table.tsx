"use client";

import { AlertTriangle, ArrowDown, ArrowUp, Search } from "lucide-react";
import { useDeferredValue, useState } from "react";

import { SourceBadges } from "@/components/sources/badges";
import { usePreview } from "@/components/sources/use-job";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  formatDate,
  formatRupees,
  formatTime,
  type PreviewFilters,
  type TransactionRow,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const ANY = "__any__";
const PAGE_SIZE = 25;

type SortKey = NonNullable<PreviewFilters["sort"]>;

interface Filters {
  account: string;
  direction: string;
  paymentMode: string;
  onlyReview: boolean;
  dateFrom: string;
  dateTo: string;
  search: string;
}

const EMPTY: Filters = {
  account: ANY,
  direction: ANY,
  paymentMode: ANY,
  onlyReview: false,
  dateFrom: "",
  dateTo: "",
  search: "",
};

export function PreviewTable({
  jobId,
  onSelect,
  selectedId,
}: {
  jobId: string;
  onSelect: (row: TransactionRow) => void;
  selectedId?: string | null;
}) {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<SortKey>("date");
  const [order, setOrder] = useState<"asc" | "desc">("asc");

  // Typing should not fire a request per keystroke, and should not reset the
  // page under the user's fingers either. Deferring the search term does both
  // without a debounce timer to clean up.
  const search = useDeferredValue(filters.search);

  /** Changing any filter starts again at the first page. */
  const update = <K extends keyof Filters>(key: K, value: Filters[K]) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setOffset(0);
  };

  const { page, error, loading } = usePreview(jobId, {
    limit: PAGE_SIZE,
    offset,
    sort,
    order,
    account: filters.account === ANY ? undefined : filters.account,
    direction: filters.direction === ANY ? undefined : filters.direction,
    payment_mode: filters.paymentMode === ANY ? undefined : filters.paymentMode,
    needs_review: filters.onlyReview ? true : undefined,
    date_from: filters.dateFrom || undefined,
    date_to: filters.dateTo || undefined,
    search: search || undefined,
  });

  const toggleSort = (key: SortKey) => {
    if (sort === key) setOrder(order === "asc" ? "desc" : "asc");
    else {
      setSort(key);
      setOrder("asc");
    }
    setOffset(0);
  };

  const total = page?.total ?? 0;
  const showing = page?.rows.length ?? 0;

  return (
    <Card>
      <CardHeader className="gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>
            Transactions
            {page && (
              <span className="ml-2 text-sm font-normal tabular-nums text-muted-foreground">
                {total.toLocaleString("en-IN")}
              </span>
            )}
          </CardTitle>
          <div className="relative w-full sm:w-72">
            <Search
              className="absolute left-2.5 top-2.5 size-4 text-muted-foreground"
              aria-hidden
            />
            <Input
              value={filters.search}
              onChange={(e) => update("search", e.target.value)}
              placeholder="Search merchant, narration, reference…"
              className="pl-8"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <Filter
            label="Account"
            value={filters.account}
            onChange={(v) => update("account", v)}
            options={page?.accounts ?? []}
            anyLabel="All accounts"
          />
          <Filter
            label="Direction"
            value={filters.direction}
            onChange={(v) => update("direction", v)}
            options={["debit", "credit"]}
            anyLabel="Both"
          />
          <Filter
            label="Mode"
            value={filters.paymentMode}
            onChange={(v) => update("paymentMode", v)}
            options={page?.payment_modes ?? []}
            anyLabel="All modes"
          />
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">From</Label>
            <Input
              type="date"
              value={filters.dateFrom}
              className="h-9 w-[9.5rem]"
              onChange={(e) => update("dateFrom", e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">To</Label>
            <Input
              type="date"
              value={filters.dateTo}
              className="h-9 w-[9.5rem]"
              onChange={(e) => update("dateTo", e.target.value)}
            />
          </div>
          <Button
            type="button"
            size="sm"
            variant={filters.onlyReview ? "default" : "outline"}
            onClick={() => update("onlyReview", !filters.onlyReview)}
          >
            <AlertTriangle className="size-3.5" aria-hidden />
            Needs review
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50 text-left">
                <SortableHeader
                  label="Date"
                  active={sort === "date"}
                  order={order}
                  onClick={() => toggleSort("date")}
                />
                <th className="px-3 py-2 font-medium">Description</th>
                <SortableHeader
                  label="Merchant"
                  active={sort === "merchant"}
                  order={order}
                  onClick={() => toggleSort("merchant")}
                />
                <SortableHeader
                  label="Account"
                  active={sort === "account"}
                  order={order}
                  onClick={() => toggleSort("account")}
                />
                <th className="px-3 py-2 font-medium">Mode</th>
                <th className="px-3 py-2 font-medium">Sources</th>
                <SortableHeader
                  label="Amount"
                  active={sort === "amount"}
                  order={order}
                  onClick={() => toggleSort("amount")}
                  align="right"
                />
              </tr>
            </thead>
            <tbody>
              {loading &&
                !page &&
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} className="border-b last:border-0">
                    <td colSpan={7} className="px-3 py-2">
                      <Skeleton className="h-5 w-full" />
                    </td>
                  </tr>
                ))}

              {page?.rows.map((row) => (
                <tr
                  key={row.txn_id}
                  onClick={() => onSelect(row)}
                  className={cn(
                    "cursor-pointer border-b transition-colors last:border-0 hover:bg-muted/50",
                    row.needs_review && "bg-amber-50/70 dark:bg-amber-950/30",
                    selectedId === row.txn_id && "bg-primary/5",
                  )}
                >
                  <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                    {formatDate(row.date)}
                    {row.time && (
                      <span className="ml-1 text-xs text-muted-foreground">
                        {formatTime(row.time)}
                      </span>
                    )}
                  </td>
                  <td className="max-w-md px-3 py-2">
                    <div className="truncate" title={row.raw_description}>
                      {row.raw_description}
                    </div>
                    <div className="mt-0.5 flex flex-wrap gap-1">
                      {row.needs_review && <Flag tone="warn">review</Flag>}
                      {row.is_transfer && <Flag>transfer</Flag>}
                      {row.is_refund && <Flag>refund</Flag>}
                      {row.is_fee && <Flag>fee</Flag>}
                      {row.is_cash_withdrawal && <Flag>cash</Flag>}
                      {row.emi_flag && <Flag>EMI</Flag>}
                    </div>
                  </td>
                  <td className="px-3 py-2">{row.merchant ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">
                    {row.account}
                  </td>
                  <td className="px-3 py-2">
                    <Badge variant="secondary" className="font-normal">
                      {row.payment_mode}
                    </Badge>
                  </td>
                  <td className="px-3 py-2">
                    <SourceBadges linkedSources={row.linked_sources} />
                  </td>
                  <td
                    className={cn(
                      "whitespace-nowrap px-3 py-2 text-right font-medium tabular-nums",
                      row.direction === "credit" && "text-emerald-600",
                    )}
                  >
                    {row.direction === "credit" ? "+" : "−"}
                    {formatRupees(row.amount)}
                  </td>
                </tr>
              ))}

              {page && page.rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-3 py-10 text-center text-muted-foreground">
                    No transactions match these filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span className="tabular-nums">
            {total === 0
              ? "Nothing to show"
              : `${offset + 1}–${offset + showing} of ${total.toLocaleString("en-IN")}`}
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function Filter({
  label,
  value,
  onChange,
  options,
  anyLabel,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  anyLabel: string;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-9 w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{anyLabel}</SelectItem>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function SortableHeader({
  label,
  active,
  order,
  onClick,
  align,
}: {
  label: string;
  active: boolean;
  order: "asc" | "desc";
  onClick: () => void;
  align?: "right";
}) {
  const Arrow = order === "asc" ? ArrowUp : ArrowDown;
  return (
    <th className={cn("px-3 py-2 font-medium", align === "right" && "text-right")}>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "inline-flex items-center gap-1 hover:text-foreground",
          active ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {label}
        {active && <Arrow className="size-3" aria-hidden />}
      </button>
    </th>
  );
}

function Flag({ children, tone }: { children: React.ReactNode; tone?: "warn" }) {
  return (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
        tone === "warn"
          ? "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200"
          : "bg-muted text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}
