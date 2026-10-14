"use client";

import { useEffect, useRef, useState } from "react";
import { ErrorNotice, LoadingState } from "@/components/dashboard/AsyncState";
import { EmptyState } from "@/components/dashboard/DataTable";
import Pagination from "@/components/documents/Pagination";
import DeleteDocumentControl from "@/components/documents/DeleteDocumentControl";
import { CARD_CLASS } from "@/components/shared/styles";
import { useAsyncData } from "@/hooks/useAsyncData";
import { clampOffset, documentKey, fetchDocuments, type DeleteResult } from "@/lib/data";

const PAGE_SIZE = 10;

/** Ingested documents with a confirm-to-delete control per row (parity with the Streamlit page). */
export default function ManagedDocuments({
  refreshSignal,
  onChanged,
}: {
  refreshSignal: number;
  onChanged?: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const { data, error, loading, reload } = useAsyncData(
    (signal) => fetchDocuments({ limit: PAGE_SIZE, offset }, signal),
    `managed:${offset}`,
  );

  const firstSignal = useRef(refreshSignal);
  useEffect(() => {
    if (refreshSignal !== firstSignal.current) reload();
  }, [refreshSignal, reload]);

  const handleDeleted = (result: DeleteResult) => {
    // Deleting the last row of the last page would otherwise leave an empty page behind.
    if (data) setOffset(clampOffset(data.total - 1, PAGE_SIZE, offset));
    setNotice(
      `Deleted ${result.source_path} (chroma=${result.chroma_deleted}, bm25=${result.bm25_deleted}, images=${result.images_deleted}).`,
    );
    reload();
    onChanged?.();
  };

  return (
    <div className="space-y-3">
      {notice && (
        <div
          role="status"
          className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
        >
          {notice}
        </div>
      )}
      {error && <ErrorNotice error={error} onRetry={reload} />}
      {!error && (loading || !data) && <LoadingState label="Loading documents..." />}
      {data && !error && (
        <>
          {data.items.length === 0 ? (
            <EmptyState message="No documents ingested yet." />
          ) : (
            <ul className="space-y-2">
              {data.items.map((doc) => (
                <li key={documentKey(doc)} className={`${CARD_CLASS} flex flex-wrap items-start justify-between gap-3`}>
                  <div className="min-w-0 text-sm">
                    <div className="break-all font-mono text-slate-900 dark:text-slate-100">{doc.source_path}</div>
                    <div className="text-xs text-slate-500 dark:text-slate-400">
                      {doc.collection} · {doc.chunk_count} chunks / {doc.image_count} images
                    </div>
                  </div>
                  <DeleteDocumentControl document={doc} onDeleted={handleDeleted} />
                </li>
              ))}
            </ul>
          )}
          {data.total > 0 && (
            <Pagination total={data.total} limit={PAGE_SIZE} offset={data.offset} onOffsetChange={setOffset} />
          )}
        </>
      )}
    </div>
  );
}
