"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError, api, type PendingMapping } from "@/lib/api";
import { cn } from "@/lib/utils";

const IGNORE = "ignore";

const AMOUNT_STYLE_LABELS: Record<string, string> = {
  debit_credit: "Separate debit and credit columns",
  signed: "One amount column, negative for money out",
  dr_cr_suffix: "One amount column ending in Dr or Cr",
  dr_cr_flag: "One amount column, with a separate Dr/Cr column",
};

function initialAssignments(mapping: PendingMapping | null): Record<string, string> {
  if (!mapping) return {};
  const assignments: Record<string, string> = {};
  for (const header of mapping.headers) assignments[header] = IGNORE;
  for (const suggestion of mapping.suggestions) {
    if (mapping.headers.includes(suggestion.header)) {
      assignments[suggestion.header] = suggestion.field_name;
    }
  }
  return assignments;
}

/**
 * The dialog for a layout nothing recognised.
 *
 * It shows the first ten rows *as extracted*, because the fastest way to tell
 * which column is which is to look at what is in it. Confirming saves the
 * answer as a template, so this file's layout is free from then on.
 */
export function MappingDialog({
  jobId,
  mapping,
  onClose,
  onConfirmed,
}: {
  jobId: string;
  mapping: PendingMapping | null;
  onClose: () => void;
  onConfirmed: () => void;
}) {
  const [assignments, setAssignments] = useState(() => initialAssignments(mapping));
  const [amountStyle, setAmountStyle] = useState(
    () => mapping?.amount_style ?? "debit_credit",
  );
  const [saveTemplate, setSaveTemplate] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const columns = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [header, field] of Object.entries(assignments)) {
      if (field && field !== IGNORE) out[field] = header;
    }
    return out;
  }, [assignments]);

  const problems = useMemo(() => {
    const list: string[] = [];
    if (!columns.date) list.push("a date column");
    if (!columns.description) list.push("a description column");
    if (!columns.debit && !columns.credit && !columns.amount) {
      list.push("an amount column (debit/credit or a single amount)");
    }
    const used = Object.values(assignments).filter((f) => f !== IGNORE);
    if (used.length !== new Set(used).size) {
      list.push("each field mapped to only one column");
    }
    return list;
  }, [columns, assignments]);

  if (!mapping) return null;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.confirmMapping(jobId, mapping.doc_id, {
        columns,
        amount_style: amountStyle,
        save_as_template: saveTemplate,
      });
      onConfirmed();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const suggestionFor = (header: string) =>
    mapping.suggestions.find((s) => s.header === header);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] gap-0 overflow-hidden p-0 sm:max-w-5xl">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle>Which column is which?</DialogTitle>
          <DialogDescription>
            We have not seen {mapping.filename}&apos;s layout before. Confirm the
            columns once and every future file in this format is read
            automatically.
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[60vh] space-y-6 overflow-y-auto px-6 py-5">
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  {mapping.headers.map((header) => {
                    const uncertain = mapping.uncertain.includes(header);
                    return (
                      <th key={header} className="min-w-44 p-2 text-left align-top">
                        <div
                          className={cn(
                            "mb-2 truncate font-medium",
                            uncertain && "text-amber-600",
                          )}
                          title={header}
                        >
                          {header}
                        </div>
                        <Select
                          value={assignments[header] ?? IGNORE}
                          onValueChange={(value) =>
                            setAssignments((prev) => ({ ...prev, [header]: value }))
                          }
                        >
                          <SelectTrigger className="h-8 w-full text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value={IGNORE}>— ignore —</SelectItem>
                            {mapping.available_fields
                              .filter((f) => f.name !== IGNORE)
                              .map((f) => (
                                <SelectItem key={f.name} value={f.name}>
                                  {f.name.replace(/_/g, " ")}
                                </SelectItem>
                              ))}
                          </SelectContent>
                        </Select>
                        {suggestionFor(header)?.reason && (
                          <p className="mt-1 truncate text-[10px] font-normal text-muted-foreground">
                            {suggestionFor(header)!.reason}
                          </p>
                        )}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {mapping.sample_rows.map((row, index) => (
                  <tr key={index} className="border-b last:border-0">
                    {mapping.headers.map((header, column) => (
                      <td
                        key={header}
                        className="max-w-56 truncate p-2 font-mono text-xs text-muted-foreground"
                        title={row[column]}
                      >
                        {row[column]}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="space-y-2">
            <Label>How does this file show amounts?</Label>
            <Select value={amountStyle} onValueChange={setAmountStyle}>
              <SelectTrigger className="w-full sm:w-[28rem]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {mapping.amount_styles.map((style) => (
                  <SelectItem key={style} value={style}>
                    {AMOUNT_STYLE_LABELS[style] ?? style}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <Checkbox
              id="save-template"
              checked={saveTemplate}
              onCheckedChange={(v) => setSaveTemplate(v === true)}
            />
            <Label htmlFor="save-template" className="font-normal">
              Remember this layout, so files like it are read automatically
            </Label>
          </div>

          {problems.length > 0 && (
            <p className="text-sm text-amber-600">
              Still needed: {problems.join(", ")}.
            </p>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter className="border-t px-6 py-4">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={problems.length > 0 || busy}>
            {busy ? "Reading the file…" : "Confirm and continue"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
