import { apiFetch } from "./api";
import { ApiError, parseErrorEnvelope, toApiError } from "./errors";
import { buildQuery, getJson } from "./queries";
import { normalizeTask, type TaskRecord } from "./tasks";
import type { CollectionStats, Page } from "./types";

type Rec = Record<string, unknown>;

function isRecord(value: unknown): value is Rec {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function int(value: unknown): number {
  return num(value) ?? 0;
}

export interface DocumentSummary {
  source_path: string;
  collection: string;
  chunk_count: number;
  image_count: number;
  ingested_at: string | null;
  doc_hash: string | null;
  title: string | null;
}

export interface ChunkRecord {
  id: string | null;
  text: string;
  metadata: Record<string, unknown>;
}

export interface ImageInfo {
  image_id: string;
  collection: string | null;
  doc_hash: string | null;
  page_num: number | null;
  created_at: string | null;
}

export interface DocumentDetail extends DocumentSummary {
  chunks: ChunkRecord[];
  images: ImageInfo[];
}

export interface DeleteResult {
  source_path: string;
  collection: string;
  chroma_deleted: number;
  bm25_deleted: number;
  images_deleted: number;
  integrity_removed: boolean;
}

export interface UploadedFile {
  name: string;
  size: number;
}

export interface TaskSubmitted {
  task_id: string;
  status: string;
  task: TaskRecord;
}

export function normalizeDocumentSummary(raw: unknown): DocumentSummary {
  const r = isRecord(raw) ? raw : {};
  return {
    source_path: str(r.source_path) ?? "",
    collection: str(r.collection) ?? "",
    chunk_count: int(r.chunk_count),
    image_count: int(r.image_count),
    ingested_at: str(r.ingested_at),
    doc_hash: str(r.doc_hash),
    title: str(r.title),
  };
}

export function normalizeImageInfo(raw: unknown): ImageInfo {
  const r = isRecord(raw) ? raw : {};
  return {
    image_id: str(r.image_id) ?? "",
    collection: str(r.collection),
    doc_hash: str(r.doc_hash),
    page_num: num(r.page_num),
    created_at: str(r.created_at),
  };
}

export function normalizeDocumentDetail(raw: unknown): DocumentDetail {
  const r = isRecord(raw) ? raw : {};
  return {
    ...normalizeDocumentSummary(r),
    chunks: (Array.isArray(r.chunks) ? r.chunks : []).map((chunk) => {
      const c = isRecord(chunk) ? chunk : {};
      return { id: str(c.id), text: str(c.text) ?? "", metadata: isRecord(c.metadata) ? c.metadata : {} };
    }),
    images: (Array.isArray(r.images) ? r.images : []).map(normalizeImageInfo),
  };
}

export function normalizeDocumentPage(raw: unknown): Page<DocumentSummary> {
  const r = isRecord(raw) ? raw : {};
  const items = (Array.isArray(r.items) ? r.items : []).map(normalizeDocumentSummary);
  return {
    items,
    total: num(r.total) ?? items.length,
    limit: num(r.limit) ?? items.length,
    offset: int(r.offset),
  };
}

export function normalizeCollections(raw: unknown): CollectionStats[] {
  const r = isRecord(raw) ? raw : {};
  return (Array.isArray(r.items) ? r.items : []).map((item) => {
    const c = isRecord(item) ? item : {};
    return {
      name: str(c.name) ?? "",
      document_count: int(c.document_count),
      chunk_count: int(c.chunk_count),
      image_count: int(c.image_count),
    };
  });
}

export function normalizeDeleteResult(raw: unknown): DeleteResult {
  const r = isRecord(raw) ? raw : {};
  return {
    source_path: str(r.source_path) ?? "",
    collection: str(r.collection) ?? "",
    chroma_deleted: int(r.chroma_deleted),
    bm25_deleted: int(r.bm25_deleted),
    images_deleted: int(r.images_deleted),
    integrity_removed: r.integrity_removed === true,
  };
}

/** `source_path` may contain `/` (even a leading one): encode each segment, keep separators. */
export function documentPath(sourcePath: string): string {
  return sourcePath.split("/").map(encodeURIComponent).join("/");
}

export function imagePath(imageId: string): string {
  return `/api/v1/images/${encodeURIComponent(imageId)}`;
}

/** Send a non-GET request and surface every failure as an `ApiError` with the unified message. */
export async function requestJson(
  method: "POST" | "DELETE",
  path: string,
  body?: BodyInit | null,
  options: { json?: unknown; signal?: AbortSignal } = {},
): Promise<unknown> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let payload = body ?? undefined;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(options.json);
  }
  let response: Response;
  try {
    response = await apiFetch(path, { method, headers, body: payload, signal: options.signal });
  } catch (error) {
    throw toApiError(error);
  }

  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    parsed = null;
  }
  if (!response.ok) {
    const { code, message, details } = parseErrorEnvelope(parsed, response.status);
    throw new ApiError(response.status, code, message, details);
  }
  return parsed;
}

export async function fetchCollections(signal?: AbortSignal): Promise<CollectionStats[]> {
  return normalizeCollections(await getJson("/api/v1/collections", signal));
}

export interface DocumentListParams {
  collection?: string;
  keyword?: string;
  limit?: number;
  offset?: number;
}

export async function fetchDocuments(
  params: DocumentListParams = {},
  signal?: AbortSignal,
): Promise<Page<DocumentSummary>> {
  const query = buildQuery({
    collection: params.collection,
    keyword: params.keyword?.trim(),
    limit: params.limit,
    offset: params.offset,
  });
  return normalizeDocumentPage(await getJson(`/api/v1/documents${query}`, signal));
}

export async function fetchDocument(
  sourcePath: string,
  collection: string,
  signal?: AbortSignal,
): Promise<DocumentDetail> {
  const query = buildQuery({ collection });
  return normalizeDocumentDetail(await getJson(`/api/v1/documents/${documentPath(sourcePath)}${query}`, signal));
}

export async function deleteDocument(sourcePath: string, collection: string): Promise<DeleteResult> {
  const query = buildQuery({ collection });
  return normalizeDeleteResult(await requestJson("DELETE", `/api/v1/documents/${documentPath(sourcePath)}${query}`));
}

/** Fetch image bytes with the auth header (an `<img src>` cannot send it). */
export async function fetchImageBlob(imageId: string, signal?: AbortSignal): Promise<Blob> {
  let response: Response;
  try {
    response = await apiFetch(imagePath(imageId), { signal });
  } catch (error) {
    throw toApiError(error);
  }
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    const { code, message, details } = parseErrorEnvelope(body, response.status);
    throw new ApiError(response.status, code, message, details);
  }
  return response.blob();
}

/** Multipart body for `POST /api/v1/uploads` (field name `files`, repeated per file). */
export function buildUploadFormData(files: readonly File[]): FormData {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  return form;
}

export async function uploadFiles(files: readonly File[], signal?: AbortSignal): Promise<UploadedFile[]> {
  const raw = await requestJson("POST", "/api/v1/uploads", buildUploadFormData(files), { signal });
  const list = isRecord(raw) && Array.isArray(raw.files) ? raw.files : [];
  return list.map((item) => {
    const f = isRecord(item) ? item : {};
    return { name: str(f.name) ?? "", size: int(f.size) };
  });
}

export interface IngestionParams {
  files: string[];
  collection: string;
  force: boolean;
}

export function buildIngestionRequest(params: IngestionParams): IngestionParams {
  return {
    files: Array.from(new Set(params.files)),
    collection: params.collection.trim() || "default",
    force: params.force,
  };
}

export function normalizeTaskSubmitted(raw: unknown): TaskSubmitted {
  const r = isRecord(raw) ? raw : {};
  const task = normalizeTask(r.task);
  return { task_id: str(r.task_id) ?? task.task_id, status: str(r.status) ?? task.status, task };
}

export async function submitIngestion(params: IngestionParams): Promise<TaskSubmitted> {
  return normalizeTaskSubmitted(
    await requestJson("POST", "/api/v1/ingestions", null, { json: buildIngestionRequest(params) }),
  );
}

/** Id of the already-running task carried by a 409 conflict, if any. */
export function conflictTaskId(error: ApiError): string | null {
  if (error.status !== 409 || !isRecord(error.details)) return null;
  return str(error.details.task_id);
}

export interface IngestionResultError {
  path: string;
  message: string;
}

export interface IngestionResult {
  files: number;
  chunks: number;
  images: number;
  failed: number;
  errors: IngestionResultError[];
}

/** Normalize `detail.result` of an ingestion task (errors arrive as `{path, message}`). */
export function normalizeIngestionResult(raw: Record<string, unknown> | null): IngestionResult | null {
  if (!raw) return null;
  const errors = (Array.isArray(raw.errors) ? raw.errors : []).map((item): IngestionResultError => {
    if (Array.isArray(item)) return { path: String(item[0] ?? ""), message: String(item[1] ?? "") };
    const e = isRecord(item) ? item : {};
    return { path: str(e.path) ?? "", message: str(e.message) ?? "" };
  });
  return {
    files: int(raw.files),
    chunks: int(raw.chunks),
    images: int(raw.images),
    failed: int(raw.failed),
    errors,
  };
}

export interface PageInfo {
  page: number;
  pages: number;
  from: number;
  to: number;
  hasPrev: boolean;
  hasNext: boolean;
}

/** 1-based page math for an offset/limit window over `total` rows. */
export function pageInfo(total: number, limit: number, offset: number): PageInfo {
  const size = Math.max(1, limit);
  const pages = Math.max(1, Math.ceil(total / size));
  const page = Math.min(pages, Math.floor(Math.max(0, offset) / size) + 1);
  const from = total === 0 ? 0 : (page - 1) * size + 1;
  const to = Math.min(total, page * size);
  return { page, pages, from, to, hasPrev: page > 1, hasNext: page < pages };
}

/** Offset that stays inside the data, e.g. after deleting the last row of the last page. */
export function clampOffset(total: number, limit: number, offset: number): number {
  const size = Math.max(1, limit);
  const lastOffset = Math.max(0, Math.ceil(total / size) - 1) * size;
  return Math.max(0, Math.min(offset, lastOffset));
}

/** Stable identity of a document across collections. */
export function documentKey(doc: Pick<DocumentSummary, "collection" | "source_path">): string {
  return `${doc.collection}\u0000${doc.source_path}`;
}

/** Metadata keys the Streamlit data browser shows for each chunk. */
export const CHUNK_METADATA_KEYS = ["title", "summary", "tags", "page", "image_refs", "collection"] as const;

export function pickChunkMetadata(metadata: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of CHUNK_METADATA_KEYS) {
    const value = metadata[key];
    if (value !== null && value !== undefined) out[key] = value;
  }
  return out;
}

export function truncateText(text: string, max = 800): { text: string; truncated: boolean } {
  return text.length > max ? { text: text.slice(0, max), truncated: true } : { text, truncated: false };
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export const NEW_COLLECTION = "__new__";

/** Target collection from the select + free-text pair; blank names fall back to `default`. */
export function resolveCollection(choice: string, newName: string): string {
  const name = choice === NEW_COLLECTION ? newName.trim() : choice.trim();
  return name || "default";
}
