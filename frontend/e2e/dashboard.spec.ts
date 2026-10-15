import { expect, test } from "@playwright/test";
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
