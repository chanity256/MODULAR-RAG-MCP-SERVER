import { expect, test } from "@playwright/test";

test.describe("Query Playground", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/query-playground");
    await expect(page.getByRole("heading", { name: "Query Playground" })).toBeVisible();
  });

  test("runs a normal search and renders the answer with citations", async ({ page }) => {
    const modes = page.getByRole("group", { name: "Search mode" });
    await expect(modes.getByRole("button", { name: /^Normal/ })).toHaveAttribute("aria-pressed", "true");

    await page.getByPlaceholder("Ask something about the knowledge base").fill("what is rag");
    await page.getByRole("button", { name: "Search", exact: true }).click();

    await expect(page.getByText("Playground answer for: what is rag")).toBeVisible();
    const citations = page.getByRole("list", { name: "Citations" });
    await expect(citations).toContainText("guide.md");
    await expect(citations).toContainText("page 2");
    await expect(citations).toContainText("score 0.8731");
    await expect(page.getByText("Agentic search details")).toHaveCount(0);
  });

  test("keeps deep disabled with its reason and runs fast with agentic details", async ({ page }) => {
    const modes = page.getByRole("group", { name: "Search mode" });
    const deep = modes.getByRole("button", { name: /^Deep/ });
    await expect(deep).toBeDisabled();
    await expect(page.getByText(/Deep unavailable: Deep search is disabled/)).toBeVisible();

    await modes.getByRole("button", { name: /^Fast/ }).click();
    await page.getByPlaceholder("Ask something about the knowledge base").fill("fast question");
    await page.getByRole("button", { name: "Search", exact: true }).click();

    await expect(page.getByText("Playground answer for: fast question")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Agentic search details" })).toBeVisible();
    await expect(page.getByText("LLM calls", { exact: true }).locator("xpath=following-sibling::div[1]")).toHaveText("2");
  });

  test("validates an empty query", async ({ page }) => {
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Enter a query first." })).toBeVisible();
  });
});

test.describe("Query Playground deep progress stream (WS3)", () => {
  // Route stubs must exist before the page loads (capabilities drive the Deep button state).
  const STREAM_CAPS = {
    modes: ["normal", "fast", "deep"],
    default_mode: "normal",
    agentic_enabled: true,
    allow_deep: true,
    availability: {
      normal: { available: true, reason: null },
      fast: { available: true, reason: null },
      deep: { available: true, reason: null },
    },
    progress_stream: true,
  };

  const SYNC_CAPS = { ...STREAM_CAPS, progress_stream: false };

  test("streams deep progress logs over the WS endpoint and renders the result", async ({ page }) => {
    await page.route("**/api/v1/search/capabilities", (route) => route.fulfill({ json: STREAM_CAPS }));
    let connections = 0;
    await page.routeWebSocket("**/api/v1/search/ws", (ws) => {
      connections += 1;
      ws.onMessage((message) => {
        const frame = JSON.parse(message as string);
        if (!frame.mode) return; // cancel 帧：假后端丢弃即可
        ws.send(JSON.stringify({ type: "search_log", task_id: "t", level: "info", message: "planning search" }));
        ws.send(JSON.stringify({ type: "search_log", task_id: "t", level: "warning", message: "slow step" }));
        ws.send(
          JSON.stringify({
            type: "search_result",
            mode: "deep",
            query: frame.query,
            elapsed_ms: 9,
            markdown: "Deep WS answer",
            structured: { citations: [] },
            is_error: false,
            error: null,
          }),
        );
      });
    });

    await page.goto("/query-playground");
    await expect(page.getByRole("heading", { name: "Query Playground" })).toBeVisible();
    const modes = page.getByRole("group", { name: "Search mode" });
    await modes.getByRole("button", { name: /^Deep/ }).click();
    await page.getByPlaceholder("Ask something about the knowledge base").fill("deep question");
    await page.getByRole("button", { name: "Search", exact: true }).click();

    await expect(page.getByText("planning search")).toBeVisible();
    await expect(page.getByText("slow step")).toBeVisible();
    await expect(page.getByText("Deep WS answer")).toBeVisible();
    expect(connections).toBe(1);
  });

  test("keeps deep on the synchronous path when progress_stream is off", async ({ page }) => {
    await page.route("**/api/v1/search/capabilities", (route) => route.fulfill({ json: SYNC_CAPS }));
    await page.route("**/api/v1/search", (route) =>
      route.fulfill({
        json: {
          mode: "deep", query: "sync question", elapsed_ms: 8,
          markdown: "Sync deep answer", structured: { citations: [] }, is_error: false, error: null,
        },
      }),
    );
    let connections = 0;
    await page.routeWebSocket("**/api/v1/search/ws", () => {
      connections += 1;
    });

    await page.goto("/query-playground");
    await expect(page.getByRole("heading", { name: "Query Playground" })).toBeVisible();
    const modes = page.getByRole("group", { name: "Search mode" });
    await modes.getByRole("button", { name: /^Deep/ }).click();
    await page.getByPlaceholder("Ask something about the knowledge base").fill("sync question");
    await page.getByRole("button", { name: "Search", exact: true }).click();

    await expect(page.getByText("Sync deep answer")).toBeVisible();
    expect(connections).toBe(0);
  });
});
