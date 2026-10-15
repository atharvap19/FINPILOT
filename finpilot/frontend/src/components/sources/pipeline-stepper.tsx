"use client";

import { Check } from "lucide-react";

import { STAGE_LABELS, type JobState, type Stage } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Detect → Extract → Map → Normalize → Validate → Merge → Link → Done.
 *
 * Driven entirely by the polled job state, so it is the same view whether the
 * page was open the whole time or opened halfway through.
 */
export function PipelineStepper({ job }: { job: JobState }) {
  const stages = job.stages.length ? job.stages : (Object.keys(STAGE_LABELS) as Stage[]);
  const current = stages.indexOf(job.stage);
  const failed = job.status === "failed";

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5 overflow-x-auto pb-1">
        {stages.map((stage, index) => {
          const done = index < current || job.status === "done";
          const active = index === current && !done;
          return (
            <div key={stage} className="flex shrink-0 items-center gap-1.5">
              <div
                className={cn(
                  "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
                  done && "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
                  active && !failed && "border-primary bg-primary/10 font-medium text-primary",
                  active && failed && "border-destructive bg-destructive/10 text-destructive",
                  !done && !active && "border-muted text-muted-foreground",
                )}
              >
                {done ? (
                  <Check className="size-3" aria-hidden />
                ) : (
                  <span
                    className={cn(
                      "size-1.5 rounded-full",
                      active && !failed && "animate-pulse bg-primary",
                      active && failed && "bg-destructive",
                      !active && "bg-muted-foreground/40",
                    )}
                  />
                )}
                {STAGE_LABELS[stage]}
              </div>
              {index < stages.length - 1 && (
                <div
                  className={cn(
                    "h-px w-3",
                    index < current ? "bg-emerald-300" : "bg-muted",
                  )}
                />
              )}
            </div>
          );
        })}
      </div>

      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-500",
            failed ? "bg-destructive" : "bg-primary",
          )}
          style={{ width: `${Math.round(job.progress * 100)}%` }}
        />
      </div>
    </div>
  );
}
