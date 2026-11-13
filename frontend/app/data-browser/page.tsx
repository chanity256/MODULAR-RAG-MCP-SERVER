"use client";

import { useState } from "react";
import { ErrorNotice, LoadingState } from "@/components/dashboard/AsyncState";
import DataTable, { type Column } from "@/components/dashboard/DataTable";
import { PageHeader } from "@/components/dashboard/Section";
import DocumentDetailView from "@/components/documents/DocumentDetailView";
import Pagination from "@/components/documents/Pagination";
import { INPUT_CLASS } from "@/components/shared/styles";
import { useAsyncData } from "@/hooks/useAsyncData";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import {
  clampOffset,
  documentKey,
  fetchCollections,
  fetchDocuments,
  type DeleteResult,
  type DocumentSummary,
} from "@/lib/data";
import { formatTime } from "@/lib/traces";

const PAGE_SIZE = 20;

const COLUMNS: Column<DocumentSummary>[] = [
  {
    key: "document",
    header: "Document",
    render: (r) => (
      <span title={r.source_path} className="block max-w-[28rem] truncate">
        {r.title || r.source_path}
      </span>
    ),
  },
  { key: "collection", header: "Collection", render: (r) => r.collection },
  { key: "chunks", header: "Chunks", render: (r) => r.chunk_count, align: "right" },
  { key: "images", header: "Images", render: (r) => r.image_count, align: "right" },
  { key: "ingested", header: "Ingested", render: (r) => formatTime(r.ingested_at) },
];

export default function DataBrowserPage() {
  const [collection, setCollection] = useState("");
  const [keyword, setKeyword] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<DocumentSummary | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const debounced = useDebouncedValue(keyword);

  const collections = useAsyncData((signal) => fetchCollections(signal), "collections");
  const documents = useAsyncData(
    (signal) => fetchDocuments({ collection, keyword: debounced, limit: PAGE_SIZE, offset }, signal),
    `docs:${collection}:${debounced}:${offset}`,
  );

  const page = documents.data;
  const reloadAll = () => {
    documents.reload();
    collections.reload();
  };

  const handleDeleted = (result: DeleteResult) => {
    setSelected(null);
    if (page) setOffset(clampOffset(page.total - 1, PAGE_SIZE, offset));
    setNotice(
      `Deleted ${result.source_path} (chroma=${result.chroma_deleted}, bm25=${result.bm25_deleted}, images=${result.images_deleted}).`,
    );
    reloadAll();
  };

  const collectionNames = collections.data?.map((c) => c.name) ?? [];
  if (collection && !collectionNames.includes(collection) && collections.data) collectionNames.push(collection);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <PageHeader
        title="Data Browser"
        description="Browse ingested documents, chunk details and associated images."
        actions={
          <button
            type="button"
            onClick={reloadAll}
            className="rounded border border-slate-300 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            Refresh
          </button>
        }
      />

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
          Collection
          <select
            aria-label="Collection filter"
            className={INPUT_CLASS}
            value={collection}
            onChange={(e) => {
              setCollection(e.target.value);
              setOffset(0);
              setSelected(null);
            }}
          >
            <option value="">All collections</option>
            {collectionNames.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <input
          type="search"
          aria-label="Search documents"
          placeholder="Search source_path / title"
          className={`${INPUT_CLASS} w-72`}
          value={keyword}
          onChange={(e) => {
            setKeyword(e.target.value);
            setOffset(0);
          }}
        />
      </div>

      {notice && (
        <div
          role="status"
          className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
        >
          {notice}
        </div>
      )}

      {collections.error && !documents.error && <ErrorNotice error={collections.error} onRetry={collections.reload} />}
      {documents.error && <ErrorNotice error={documents.error} onRetry={documents.reload} />}
      {!documents.error && (documents.loading || !page) && <LoadingState label="Loading documents..." />}

      {page && !documents.error && (
        <div className="space-y-3">
          <DataTable
            columns={COLUMNS}
            rows={page.items}
            rowKey={documentKey}
            selectedKey={selected ? documentKey(selected) : null}
            onRowClick={setSelected}
            emptyMessage={
              page.total === 0 && !debounced && !collection
                ? "No documents ingested yet. Use the Ingestion Manager to add some."
                : "No matching documents."
            }
          />
          {page.total > 0 && (
            <Pagination total={page.total} limit={PAGE_SIZE} offset={page.offset} onOffsetChange={setOffset} />
          )}
        </div>
      )}

      {selected && (
        <DocumentDetailView document={selected} onDeleted={handleDeleted} key={documentKey(selected)} />
      )}
    </div>
  );
}
