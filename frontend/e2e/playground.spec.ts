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
