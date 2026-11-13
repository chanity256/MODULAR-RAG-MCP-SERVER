"use client";

import type { ReactNode } from "react";
import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchTrace } from "@/lib/queries";
import type { TraceDetail } from "@/lib/types";
import { ErrorNotice, LoadingState } from "./AsyncState";

export default function TraceDetailLoader({
  traceId,
  children,
}: {
  traceId: string;
  children: (trace: TraceDetail) => ReactNode;
}) {
  const { data, error, loading, reload } = useAsyncData((signal) => fetchTrace(traceId, signal), traceId);
  if (error) return <ErrorNotice error={error} onRetry={reload} />;
  if (loading || !data) return <LoadingState label="Loading trace..." />;
  return <>{children(data)}</>;
}
