import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildIngestionRequest,
  buildUploadFormData,
  clampOffset,
  conflictTaskId,
  deleteDocument,
  documentPath,
  fetchDocument,
  fetchDocuments,
  normalizeDocumentDetail,
  normalizeIngestionResult,
  normalizeDocumentPage,
  pageInfo,
  pickChunkMetadata,
  resolveCollection,
  submitIngestion,
  truncateText,
  uploadFiles,
  NEW_COLLECTION,
} from "./data";
import { ApiError } from "./errors";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("documentPath", () => {
  it("encodes segments but keeps slashes, including a leading one", () => {
    expect(documentPath("docs/my file#1.pdf")).toBe("docs/my%20file%231.pdf");
    expect(documentPath("/abs/path/a b.md")).toBe("/abs/path/a%20b.md");
  });
});

describe("pagination", () => {
  it("computes the window for a middle page", () => {
    expect(pageInfo(95, 20, 40)).toEqual({ page: 3, pages: 5, from: 41, to: 60, hasPrev: true, hasNext: true });
  });

  it("handles the last partial page and empty data", () => {
    expect(pageInfo(95, 20, 80)).toMatchObject({ page: 5, from: 81, to: 95, hasNext: false });
    expect(pageInfo(0, 20, 0)).toEqual({ page: 1, pages: 1, from: 0, to: 0, hasPrev: false, hasNext: false });
  });

  it("clamps an offset that fell off the end (e.g. after a delete)", () => {
    expect(clampOffset(40, 20, 40)).toBe(20);
    expect(clampOffset(41, 20, 40)).toBe(40);
    expect(clampOffset(0, 20, 40)).toBe(0);
    expect(clampOffset(100, 20, 40)).toBe(40);
  });
});

describe("listing", () => {
  it("sends collection/keyword/limit/offset and skips empty filters", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ items: [], total: 0, limit: 20, offset: 0 }));
    vi.stubGlobal("fetch", fetchMock);
    await fetchDocuments({ collection: "", keyword: "  ", limit: 20, offset: 0 });
    await fetchDocuments({ collection: "kb", keyword: " cats ", limit: 20, offset: 40 });
    const urls = fetchMock.mock.calls.map((c) => (c as unknown as [string])[0]);
    expect(urls[0]).toBe("/api/v1/documents?limit=20&offset=0");
    expect(urls[1]).toBe("/api/v1/documents?collection=kb&keyword=cats&limit=20&offset=40");
  });

  it("normalizes pages defensively", () => {
    const page = normalizeDocumentPage({
      items: [{ source_path: "a.md", collection: "kb", chunk_count: 3 }, null],
      total: 2,
    });
    expect(page.items[0]).toMatchObject({ source_path: "a.md", image_count: 0, title: null });
    expect(page.items[1].source_path).toBe("");
    expect(page.total).toBe(2);
  });

  it("fetches a detail with an encoded path and collection query", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        source_path: "a/b.md",
        collection: "kb",
        chunk_count: 1,
        chunks: [{ id: "c1", text: "hello", metadata: { title: "T", noise: 1 } }],
        images: [{ image_id: "img-1", page_num: 2 }],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const detail = await fetchDocument("a/b c.md", "kb");
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("/api/v1/documents/a/b%20c.md?collection=kb");
    expect(detail.chunks[0].id).toBe("c1");
    expect(detail.images[0]).toMatchObject({ image_id: "img-1", page_num: 2 });
  });

  it("surfaces the error envelope as ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ success: false, error: { code: "not_found", message: "Document 'x' not found" } }, 404)),
    );
    await expect(fetchDocument("x", "kb")).rejects.toMatchObject({
      status: 404,
      code: "not_found",
      message: "Document 'x' not found",
    });
  });

  it("maps transport failures to network_error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(deleteDocument("x", "kb")).rejects.toMatchObject({ status: 0, code: "network_error" });
  });
});

describe("chunk helpers", () => {
  it("keeps only the metadata keys the Streamlit page shows, dropping nulls", () => {
    expect(
      pickChunkMetadata({ title: "T", summary: null, tags: ["a"], page: 0, source: "x", collection: "kb" }),
    ).toEqual({ title: "T", tags: ["a"], page: 0, collection: "kb" });
  });

  it("truncates long text", () => {
    expect(truncateText("abc", 5)).toEqual({ text: "abc", truncated: false });
    expect(truncateText("abcdef", 5)).toEqual({ text: "abcde", truncated: true });
  });

  it("normalizes a detail without chunks or images", () => {
    expect(normalizeDocumentDetail({ source_path: "a", collection: "b" })).toMatchObject({ chunks: [], images: [] });
  });
});

describe("upload", () => {
  it("builds multipart data with the repeated `files` field", () => {
    const a = new File(["aa"], "a.txt", { type: "text/plain" });
    const b = new File(["bbb"], "b.md");
    const form = buildUploadFormData([a, b]);
    const files = form.getAll("files") as File[];
    expect(files.map((f) => f.name)).toEqual(["a.txt", "b.md"]);
    expect(Array.from(form.keys())).toEqual(["files", "files"]);
  });

  it("posts the form without a JSON content type and returns stored names", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ files: [{ name: "a.txt", size: 2 }] }, 201));
    vi.stubGlobal("fetch", fetchMock);
    const stored = await uploadFiles([new File(["aa"], "a.txt")]);
    expect(stored).toEqual([{ name: "a.txt", size: 2 }]);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/uploads");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("surfaces upload limit errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ success: false, error: { code: "payload_too_large", message: "too big", details: { max_upload_mb: 50 } } }, 413)),
    );
    const error = await uploadFiles([new File(["x"], "x.txt")]).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).details).toEqual({ max_upload_mb: 50 });
  });
});

describe("ingestion submit", () => {
  it("de-duplicates files and defaults a blank collection", () => {
    expect(buildIngestionRequest({ files: ["a", "b", "a"], collection: "  ", force: true })).toEqual({
      files: ["a", "b"],
      collection: "default",
      force: true,
    });
  });

  it("posts JSON and returns the task", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(
        { task_id: "t1", status: "pending", task: { task_id: "t1", kind: "ingestion", status: "pending", version: 0, detail: { params: {} } } },
        202,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const submitted = await submitIngestion({ files: ["a.pdf"], collection: "kb", force: false });
    expect(submitted.task_id).toBe("t1");
    expect(submitted.task.status).toBe("pending");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/ingestions");
    expect(JSON.parse(init.body as string)).toEqual({ files: ["a.pdf"], collection: "kb", force: false });
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("extracts the already-running task id from a 409", () => {
    expect(conflictTaskId(new ApiError(409, "conflict", "x", { task_id: "t9", files: ["a"] }))).toBe("t9");
    expect(conflictTaskId(new ApiError(409, "conflict", "x", null))).toBeNull();
    expect(conflictTaskId(new ApiError(404, "not_found", "x", { task_id: "t9" }))).toBeNull();
  });

  it("resolves the target collection from the select/new-name pair", () => {
    expect(resolveCollection("kb", "ignored")).toBe("kb");
    expect(resolveCollection(NEW_COLLECTION, "  fresh ")).toBe("fresh");
    expect(resolveCollection(NEW_COLLECTION, " ")).toBe("default");
  });

  it("normalizes results with {path,message} and tuple-shaped errors", () => {
    const result = normalizeIngestionResult({
      files: 3,
      chunks: 10,
      images: 1,
      failed: 2,
      errors: [{ path: "/u/a.pdf", message: "parse" }, ["b.pdf", "embed"]],
    });
    expect(result).toEqual({
      files: 3,
      chunks: 10,
      images: 1,
      failed: 2,
      errors: [
        { path: "/u/a.pdf", message: "parse" },
        { path: "b.pdf", message: "embed" },
      ],
    });
    expect(normalizeIngestionResult(null)).toBeNull();
  });
});

describe("deleteDocument", () => {
  it("sends DELETE with the collection query and normalizes the result", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ source_path: "a/b.md", collection: "kb", chroma_deleted: 3, bm25_deleted: 3, images_deleted: 1, integrity_removed: true }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await deleteDocument("a/b.md", "kb");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/documents/a/b.md?collection=kb");
    expect(init.method).toBe("DELETE");
    expect(result).toMatchObject({ chroma_deleted: 3, images_deleted: 1, integrity_removed: true });
  });
});
