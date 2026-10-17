"use client";

import { useCallback, useSyncExternalStore } from "react";
import useSWR from "swr";

import {
  api,
  ApiError,
  type JobState,
  type Obligation,
  type PreviewFilters,
  type PreviewPage,
  type SegmentChartData,
  type Summary,
  type TransactionDetail,
} from "@/lib/api";

const POLL_MS = 1000;
const STORAGE_KEY = "finpilot.jobId";

const TERMINAL = new Set(["done", "failed", "needs_input"]);

/**
 * Polls one job until it settles.
 *
 * Polling rather than a websocket: a poll survives a page reload and a second
 * tab, and a job takes seconds. `refreshInterval` returns 0 once the job
 * reaches a terminal state, so a finished page stops making requests.
 *
 * `needs_input` counts as terminal: the job is waiting for a password or a
 * column mapping, and no amount of polling will change that until the user
 * acts.
 */
export function useJob(jobId: string | null) {
  const { data, error, mutate, isLoading } = useSWR<JobState>(
    jobId ? ["job", jobId] : null,
    () => api.job(jobId as string),
    {
      refreshInterval: (latest) => (latest && TERMINAL.has(latest.status) ? 0 : POLL_MS),
      revalidateOnFocus: false,
    },
  );

  return {
    job: data ?? null,
    error: error ? String(error.message ?? error) : null,
    refresh: useCallback(() => mutate(), [mutate]),
    loading: isLoading,
    running: data?.status === "queued" || data?.status === "running",
    finished: data ? TERMINAL.has(data.status) : false,
  };
}

export function useSummary(jobId: string | null, ready: boolean) {
  const { data } = useSWR<Summary>(
    jobId && ready ? ["summary", jobId] : null,
    () => api.summary(jobId as string),
    { revalidateOnFocus: false },
  );
  return data ?? null;
}

export function useObligations(jobId: string | null, ready: boolean) {
  const { data } = useSWR<Obligation[]>(
    jobId && ready ? ["obligations", jobId] : null,
    () => api.obligations(jobId as string),
    { revalidateOnFocus: false },
  );
  return data ?? [];
}

/**
 * The segmentation charts.
 *
 * A 409 here is the normal "not segmented yet" state, not a failure, so it is
 * returned as `needsRun` rather than surfaced as an error. SWR is told not to
 * retry it: no amount of refetching segments a job.
 */
export function useSegmentChart(jobId: string | null, ready: boolean) {
  const { data, error, isLoading, mutate } = useSWR<SegmentChartData>(
    jobId && ready ? ["segment-chart", jobId] : null,
    () => api.segmentChart(jobId as string),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: (err) => !(err instanceof ApiError && err.status === 409),
    },
  );
  const needsRun = error instanceof ApiError && error.status === 409;
  return {
    data: data ?? null,
    needsRun,
    error: needsRun ? null : (error as Error | undefined) ?? null,
    isLoading,
    refresh: mutate,
  };
}

export function usePreview(jobId: string | null, filters: PreviewFilters) {
  const { data, error, isLoading } = useSWR<PreviewPage>(
    jobId ? ["preview", jobId, filters] : null,
    () => api.preview(jobId as string, filters),
    { keepPreviousData: true, revalidateOnFocus: false },
  );
  return {
    page: data ?? null,
    error: error ? String(error.message ?? error) : null,
    loading: isLoading,
  };
}

export function useTransaction(jobId: string | null, txnId: string | null) {
  const { data, error } = useSWR<TransactionDetail>(
    jobId && txnId ? ["txn", jobId, txnId] : null,
    () => api.transaction(jobId as string, txnId as string),
    { revalidateOnFocus: false },
  );
  return {
    detail: data ?? null,
    error: error ? String(error.message ?? error) : null,
  };
}

// ---------------------------------------------------------------------------
// the remembered job id
// ---------------------------------------------------------------------------

const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

/**
 * The current job id, remembered across a reload.
 *
 * `useSyncExternalStore` rather than reading localStorage in an effect: the
 * server snapshot is null, the client reads the real value during hydration,
 * and other tabs stay in step through the `storage` event.
 */
export function useRememberedJobId(): [string | null, (id: string | null) => void] {
  const jobId = useSyncExternalStore(
    subscribe,
    () => window.localStorage.getItem(STORAGE_KEY),
    () => null,
  );

  const set = useCallback((id: string | null) => {
    if (id) window.localStorage.setItem(STORAGE_KEY, id);
    else window.localStorage.removeItem(STORAGE_KEY);
    for (const listener of listeners) listener();
  }, []);

  return [jobId, set];
}
