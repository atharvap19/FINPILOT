"use client";

import { KeyRound, Table2 } from "lucide-react";

import { DocumentIcon, FileStatusBadge, MethodBadge } from "@/components/sources/badges";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { formatDate, type FileStatus } from "@/lib/api";

const DOCUMENT_LABELS: Record<string, string> = {
  bank_statement: "Bank statement",
  cc_statement: "Credit card statement",
  email: "Email",
  spreadsheet: "Spreadsheet",
  bill: "Bill",
  receipt: "Receipt",
  unknown: "Unrecognised",
};

export function FileList({
  files,
  onUnlock,
  onMap,
}: {
  files: FileStatus[];
  onUnlock: (file: FileStatus) => void;
  onMap: (file: FileStatus) => void;
}) {
  if (!files.length) return null;

  // Files needing the user first: nothing else can finish until they are dealt
  // with, so burying them under forty processed emails would be unhelpful.
  const ordered = [...files].sort((a, b) => {
    const blocked = (f: FileStatus) => (f.needs_password || f.needs_mapping ? 0 : 1);
    return blocked(a) - blocked(b) || a.filename.localeCompare(b.filename);
  });

  return (
    <div className="space-y-2">
      {ordered.map((file) => (
        <Card key={file.doc_id} className="py-0 shadow-none">
          <CardContent className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
            <DocumentIcon type={file.document_type} className="shrink-0" />

            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium" title={file.filename}>
                {file.filename}
              </p>
              <p className="truncate text-xs text-muted-foreground">
                {[
                  DOCUMENT_LABELS[file.document_type] ?? file.document_type,
                  file.issuer,
                  file.account,
                  file.statement_start &&
                    `${formatDate(file.statement_start)} – ${formatDate(file.statement_end)}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              {file.issues.slice(0, 2).map((issue, index) => (
                <p
                  key={index}
                  className={
                    issue.severity === "error"
                      ? "mt-0.5 text-xs text-destructive"
                      : "mt-0.5 text-xs text-amber-600"
                  }
                >
                  {issue.message}
                </p>
              ))}
            </div>

            {file.rows > 0 && (
              <span className="shrink-0 text-sm tabular-nums text-muted-foreground">
                {file.rows} {file.rows === 1 ? "row" : "rows"}
              </span>
            )}
            <MethodBadge method={file.extraction_method} />
            <FileStatusBadge status={file.status} />

            {file.needs_password && (
              <Button size="sm" variant="outline" onClick={() => onUnlock(file)}>
                <KeyRound className="size-3.5" aria-hidden />
                Unlock
              </Button>
            )}
            {file.needs_mapping && (
              <Button size="sm" variant="outline" onClick={() => onMap(file)}>
                <Table2 className="size-3.5" aria-hidden />
                Map columns
              </Button>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
