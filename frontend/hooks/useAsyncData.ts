"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toApiError, type ApiError } from "@/lib/errors";

interface Settled<T> {
  key: string;
  data?: T;
  error?: ApiError;
}

export interface AsyncData<T> {
  data: T | undefined;
  error: ApiError | undefined;
  /** True until the first response for the current `key` arrives (refreshes keep old data). */
  loading: boolean;
  reload: () => void;
}

/**
 * Run `load` whenever `key` changes (or `reload()` / the refresh timer fires). `key` must
 * encode every input of `load`; the loader itself is read through a ref so callers can pass
 * an inline closure.
 */
export function useAsyncData<T>(
  load: (signal: AbortSignal) => Promise<T>,
  key: string,
  refreshMs = 0,
): AsyncData<T> {
  const [settled, setSettled] = useState<Settled<T> | null>(null);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);

  useEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    const controller = new AbortController();
    loadRef.current(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setSettled({ key, data });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setSettled({ key, error: toApiError(error) });
      },
    );
    return () => controller.abort();
  }, [key, tick]);

  useEffect(() => {
    if (!refreshMs) return;
    const id = window.setInterval(() => setTick((t) => t + 1), refreshMs);
    return () => window.clearInterval(id);
  }, [refreshMs]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const current = settled && settled.key === key ? settled : null;
  return { data: current?.data, error: current?.error, loading: current === null, reload };
}
