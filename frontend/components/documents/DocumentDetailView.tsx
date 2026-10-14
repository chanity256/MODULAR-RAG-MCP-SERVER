"use client";

import { useState } from "react";
import { ErrorNotice, LoadingState } from "@/components/dashboard/AsyncState";
import { EmptyState } from "@/components/dashboard/DataTable";
import { Section } from "@/components/dashboard/Section";
import { CARD_CLASS, BUTTON_SECONDARY } from "@/components/shared/styles";
import { useAsyncData } from "@/hooks/useAsyncData";
import {
  documentKey,
  fetchDocument,
  pickChunkMetadata,
  truncateText,
  type ChunkRecord,
  type DeleteResult,
  type DocumentSummary,
} from "@/lib/data";
import { formatTime } from "@/lib/traces";
import DeleteDocumentControl from "./DeleteDocumentControl";
import ImageThumb from "./ImageThumb";

function ChunkView({ chunk, index }: { chunk: ChunkRecord; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? { text: chunk.text, truncated: false } : truncateText(chunk.text);
  const metadata = pickChunkMetadata(chunk.metadata);
  return (
    <div className={CARD_CLASS}>
      <div className="text-sm font-medium text-slate-900 dark:text-slate-100">
        Chunk {index + 1} <span className="ml-1 font-mono text-xs font-normal text-slate-500">{chunk.id ?? "-"}</span>
      </div>
      <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-xs text-slate-700 dark:text-slate-300">
        {shown.text || "(empty)"}
        {shown.truncated && "…"}
      </pre>
      {(shown.truncated || expanded) && chunk.text.length > 800 && (
        <button type="button" className={`${BUTTON_SECONDARY} mt-2`} onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show less" : "Show full text"}
        </button>
      )}
      {Object.keys(metadata).length > 0 && (
        <pre className="mt-2 overflow-x-auto rounded bg-slate-50 p-2 text-[11px] text-slate-600 dark:bg-slate-900 dark:text-slate-400">
          {JSON.stringify(metadata, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function DocumentDetailView({
  document,
  onDeleted,
}: {
  document: DocumentSummary;
  onDeleted: (result: DeleteResult) => void;
}) {
  const key = documentKey(document);
  const { data, error, loading, reload } = useAsyncData(
    (signal) => fetchDocument(document.source_path, document.collection, signal),
    key,
  );

  return (
    <div className="space-y-4">
      <div className={`${CARD_CLASS} space-y-2`}>
        <h2 className="break-all text-base font-semibold text-slate-900 dark:text-slate-100">
          {document.title || document.source_path}
        </h2>
        <dl className="grid gap-x-6 gap-y-1 text-xs text-slate-600 sm:grid-cols-2 dark:text-slate-300">
          <div>
            <dt className="inline font-medium">Source: </dt>
            <dd className="inline break-all font-mono">{document.source_path}</dd>
          </div>
          <div>
            <dt className="inline font-medium">Collection: </dt>
            <dd className="inline font-mono">{document.collection}</dd>
          </div>
          <div>
            <dt className="inline font-medium">Chunks: </dt>
            <dd className="inline">{document.chunk_count}</dd>
          </div>
          <div>
            <dt className="inline font-medium">Images: </dt>
            <dd className="inline">{document.image_count}</dd>
          </div>
          {document.ingested_at && (
            <div>
              <dt className="inline font-medium">Ingested at: </dt>
              <dd className="inline">{formatTime(document.ingested_at)}</dd>
            </div>
          )}
          {document.doc_hash && (
            <div>
              <dt className="inline font-medium">Hash: </dt>
              <dd className="inline font-mono">{document.doc_hash.slice(0, 12)}</dd>
            </div>
          )}
        </dl>
        <DeleteDocumentControl key={key} document={document} onDeleted={onDeleted} />
      </div>

      {error && <ErrorNotice error={error} onRetry={reload} />}
      {!error && (loading || !data) && <LoadingState label="Loading document..." />}

      {data && (
        <>
          <Section title={`Chunks (${data.chunks.length})`}>
            {data.chunks.length === 0 ? (
              <EmptyState message="This document has no chunks." />
            ) : (
              <div className="space-y-3">
                {data.chunks.map((chunk, index) => (
                  <ChunkView key={`${chunk.id ?? "chunk"}:${index}`} chunk={chunk} index={index} />
                ))}
              </div>
            )}
          </Section>

          <Section title={`Images (${data.images.length})`}>
            {data.images.length === 0 ? (
              <EmptyState message="No associated images." />
            ) : (
              <div className="flex flex-wrap gap-3">
                {data.images.map((image) => (
                  <ImageThumb key={image.image_id} imageId={image.image_id} />
                ))}
              </div>
            )}
          </Section>
        </>
      )}
    </div>
  );
}
