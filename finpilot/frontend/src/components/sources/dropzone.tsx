"use client";

import { Upload } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// No images. Every document here is read by code, and a photograph has no
// text layer to parse -- a bill or receipt has to be a PDF.
const ACCEPT = [".csv", ".tsv", ".xlsx", ".xls", ".pdf", ".eml", ".mbox"].join(",");

const HINT = "CSV, XLSX, PDF, EML, MBOX";

export function Dropzone({
  onFiles,
  busy,
  compact,
}: {
  onFiles: (files: File[]) => void;
  busy?: boolean;
  compact?: boolean;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const accept = useCallback(
    (list: FileList | null) => {
      if (!list?.length) return;
      onFiles(Array.from(list));
    },
    [onFiles],
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!busy) accept(e.dataTransfer.files);
      }}
      className={cn(
        "rounded-xl border-2 border-dashed transition-colors",
        over ? "border-primary bg-primary/5" : "border-muted-foreground/25",
        busy && "pointer-events-none opacity-60",
        compact ? "p-4" : "p-10",
      )}
    >
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        className="sr-only"
        onChange={(e) => {
          accept(e.target.files);
          e.target.value = "";
        }}
      />
      <div
        className={cn(
          "flex items-center gap-4",
          compact ? "justify-between" : "flex-col justify-center text-center",
        )}
      >
        <div className={cn("flex items-center gap-3", !compact && "flex-col")}>
          <div className="rounded-full bg-muted p-3">
            <Upload className="size-5 text-muted-foreground" aria-hidden />
          </div>
          <div className={cn(!compact && "space-y-1")}>
            <p className="font-medium">
              {compact ? "Add more files" : "Drop your statements here"}
            </p>
            <p className="text-sm text-muted-foreground">
              {compact ? HINT : `${HINT} — several at once`}
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant={compact ? "outline" : "default"}
          onClick={() => input.current?.click()}
          disabled={busy}
        >
          Choose files
        </Button>
      </div>
    </div>
  );
}
