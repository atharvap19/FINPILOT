"use client";

/**
 * The segmentation section, directly below the exports.
 *
 * Segmentation is a separate step from ingestion -- it runs over the finished
 * ledger -- so this card has three states: not run yet (an invitation), running,
 * and the charts.
 */

import { ChartPie, Loader2 } from "lucide-react";
import { useState } from "react";

import { SegmentCharts } from "@/components/sources/segment-charts";
import { useSegmentChart } from "@/components/sources/use-job";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";

export function SegmentsCard({
  jobId,
  ready,
  onSegmented,
}: {
  jobId: string;
  ready: boolean;
  /** called after a run, so other views (the overview, habit cards) refresh */
  onSegmented?: () => void;
}) {
  const { data, needsRun, error, isLoading, refresh } = useSegmentChart(jobId, ready);
  const [running, setRunning] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [namedByModel, setNamedByModel] = useState(0);

  if (!ready) return null;

  async function run() {
    setRunning(true);
    setFailed(null);
    try {
      const result = await api.runSegmentation(jobId);
      setNamedByModel(result.named_by_model);
      await refresh();
      onSegmented?.();
    } catch (err) {
      setFailed(err instanceof Error ? err.message : "segmentation failed");
    } finally {
      setRunning(false);
    }
  }

  if (isLoading && !data && !needsRun) {
    return (
      <Card>
        <CardContent>
          <Skeleton className="h-48 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (data) return <SegmentCharts data={data} namedByModel={namedByModel} />;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Segments</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col items-start gap-3">
        <p className="text-sm text-muted-foreground">
          Group this ledger into spending patterns and name them. Nothing is
          re-read: it runs over the transactions above.
        </p>
        {failed && (
          <Alert variant="destructive">
            <AlertDescription>{failed}</AlertDescription>
          </Alert>
        )}
        {error && !needsRun && (
          <Alert variant="destructive">
            <AlertDescription>{error.message}</AlertDescription>
          </Alert>
        )}
        <Button onClick={run} disabled={running}>
          {running ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <ChartPie className="size-4" aria-hidden />
          )}
          {running ? "Finding patterns…" : "Find segments"}
        </Button>
      </CardContent>
    </Card>
  );
}
