import { expect, test, type Page, type Route } from "@playwright/test";
import { resetBackend } from "./helpers";

test.describe("Overview and traces", () => {
  test.beforeEach(async ({ request }) => {
    await resetBackend(request);
  });

  test("overview shows components, data assets and the latest traces", async ({ page }) => {
    await page.goto("/overview");
    await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();

    for (const name of ["LLM", "Embedding", "Splitter", "Reranker", "Agentic Search"]) {
      await expect(page.getByText(name, { exact: true }).first()).toBeVisible();
    }

    const collections = page.getByRole("table");
    await expect(collections.getByRole("row", { name: /e2e-docs/ })).toBeVisible();
    await expect(collections.getByRole("row", { name: /archive/ })).toBeVisible();
    await expect(page.getByText("Documents", { exact: true }).locator("xpath=following-sibling::div[1]")).toHaveText("4");

    const latestIngestion = page.locator("div.rounded-lg").filter({ hasText: "Latest ingestion" }).last();
    await expect(latestIngestion).toContainText("seed-ing-guide");
    // The newest query trace is always a chat turn (seeded, or a real one from the chat flows).
    const latestQuery = page.locator("div.rounded-lg").filter({ hasText: "Latest query" }).last();
    await expect(latestQuery).toContainText(/trace_id:\s*\S+/);
    await expect(latestQuery.getByText("chat", { exact: true })).toBeVisible();
  });

  test("ingestion traces list, detail and search", async ({ page }) => {
    await page.goto("/ingestion-traces");
    await expect(page.getByRole("heading", { name: "Ingestion Traces" })).toBeVisible();

    const guide = page.getByRole("row", { name: /guide\.md/ });
    const faq = page.getByRole("row", { name: /faq\.md/ });
    await expect(guide).toBeVisible();
    await expect(faq).toBeVisible();
    await expect(guide).toContainText("success");
    await expect(faq).toContainText("skipped");

    await guide.click();
    await expect(page.getByText("trace_id=seed-ing-guide")).toBeVisible();
    const timing = page.getByRole("list", { name: "Stage timing" });
    await expect(timing).toContainText("load");
    await expect(timing).toContainText("store");

    await faq.click();
    await expect(page.getByText("trace_id=seed-ing-faq-skipped")).toBeVisible();

    await page.getByRole("searchbox", { name: "Search ingestion traces" }).fill("guide");
    await expect(page.getByRole("row", { name: /faq\.md/ })).toHaveCount(0);
    await expect(page.getByRole("row", { name: /guide\.md/ })).toBeVisible();
  });

  test("query traces filter by source and show chat stages", async ({ page }) => {
    await page.goto("/query-traces");
    await expect(page.getByRole("heading", { name: "Query Traces" })).toBeVisible();

    const rrf = page.getByRole("row", { name: /How does RRF fuse rankings\?/ });
    const chat = page.getByRole("row", { name: /Seeded chat question about citations/ });
    await expect(rrf).toBeVisible();
    await expect(chat).toBeVisible();

    await page.getByLabel("Filter by source").selectOption("chat");
    await expect(rrf).toHaveCount(0);
    await expect(chat).toBeVisible();

    await chat.click();
    await expect(page.getByText("trace_id=seed-query-chat")).toBeVisible();
    const timing = page.getByRole("list", { name: "Stage timing" });
    for (const stage of ["chat_history", "chat_rewrite", "chat_generation"]) {
      await expect(timing).toContainText(stage);
    }
    await expect(page.getByRole("heading", { name: "Chat stages" })).toBeVisible();
    await expect(page.getByText("4 → 2")).toBeVisible();
    await expect(page.getByText("Rewritten query: Seeded rewritten query")).toBeVisible();
    await expect(page.getByText(/source=chat · retrieval=hybrid · session_id=seed-session/)).toBeVisible();

    // The default (plain hybrid) source excludes chat traces.
    await page.getByLabel("Filter by source").selectOption("hybrid");
    await expect(chat).toHaveCount(0);
    await rrf.click();
    await expect(page.getByText("trace_id=seed-query-rrf")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Dense" })).toBeVisible();
    await expect(page.getByRole("list", { name: "Stage timing" })).not.toContainText("chat_generation");
    await expect(page.getByRole("heading", { name: "Chat stages" })).toHaveCount(0);
  });
});

test.describe("Trace list pagination (WP0, stubbed pages)", () => {
  // The fake backend seeds only a handful of traces — not enough to page through — so both tests
  // stub `/api/v1/traces` with a 3-page dataset (120 rows, 50/page) via `page.route`.
  const PAGE_SIZE = 50;
  const TOTAL = 120;

  function stubSummary(kind: "query" | "ingestion", pageIndex: number) {
    return {
      trace_id: `stub-${kind}-p${pageIndex}`,
      trace_type: kind,
      started_at: "2026-10-18T00:00:00Z",
      total_elapsed_ms: 12,
      stage_count: 1,
      // `query` / `source_path` are top-level fields on TraceSummary; `metadata` is pass-through.
      query: kind === "query" ? `Stub query page ${pageIndex}` : null,
      source_path: kind === "ingestion" ? `/tmp/stub-ingestion-${pageIndex}.md` : null,
      metadata: kind === "query" ? { query: `Stub query page ${pageIndex}`, collection: null } : {},
    };
  }

  async function stubTraceList(page: Page, kind: "query" | "ingestion") {
    await page.route("**/api/v1/traces**", async (route: Route) => {
      const url = new URL(route.request().url());
      if (!url.pathname.endsWith("/api/v1/traces")) {
        // Trace detail fetch for the selected row; the list assertions don't depend on it.
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ...stubSummary(kind, 1), stages: [], timings: [] }),
        });
        return;
      }
      const offset = Number(url.searchParams.get("offset") ?? "0");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [stubSummary(kind, Math.floor(offset / PAGE_SIZE) + 1)],
          total: TOTAL,
          limit: PAGE_SIZE,
          offset,
        }),
      });
    });
  }

  test("query traces page forward and back with server offset", async ({ page }) => {
    await stubTraceList(page, "query");
    await page.goto("/query-traces");

    await expect(page.getByRole("row", { name: /Stub query page 1/ })).toBeVisible();
    await expect(page.getByText("1-50 of 120")).toBeVisible();

    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByRole("row", { name: /Stub query page 2/ })).toBeVisible();
    await expect(page.getByText("51-100 of 120")).toBeVisible();

    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByText("101-120 of 120")).toBeVisible();
    await expect(page.getByRole("button", { name: "Next" })).toBeDisabled();

    await page.getByRole("button", { name: "Previous" }).click();
    await expect(page.getByRole("row", { name: /Stub query page 2/ })).toBeVisible();

    // A filter change resets to the first page.
    await page.getByRole("searchbox", { name: "Search query traces" }).fill("stub");
    await expect(page.getByRole("row", { name: /Stub query page 1/ })).toBeVisible();
  });

  test("ingestion traces page forward and back with server offset", async ({ page }) => {
    await stubTraceList(page, "ingestion");
    await page.goto("/ingestion-traces");

    await expect(page.getByRole("row", { name: /stub-ingestion-1\.md/ })).toBeVisible();
    await expect(page.getByText("1-50 of 120")).toBeVisible();

    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByRole("row", { name: /stub-ingestion-2\.md/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Previous" })).toBeEnabled();

    await page.getByRole("button", { name: "Previous" }).click();
    await expect(page.getByRole("row", { name: /stub-ingestion-1\.md/ })).toBeVisible();
  });
});
