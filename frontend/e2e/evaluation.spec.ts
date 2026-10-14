import { expect, test } from "@playwright/test";

test.describe("Evaluation", () => {
  test("runs an evaluation and shows the report", async ({ page }) => {
    await page.goto("/evaluation");
    await expect(page.getByRole("heading", { name: "Evaluation", exact: true })).toBeVisible();

    await page.getByLabel("Evaluator backend").selectOption("custom");
    await page.getByRole("button", { name: "Run evaluation" }).click();

    const progress = page.locator("section").filter({ has: page.getByRole("heading", { name: "Progress" }) });
    await expect(progress.getByText("live (WebSocket)")).toBeVisible();
    await expect(progress.getByText("backend=custom · test set=/golden/e2e-default.json")).toBeVisible();
    await expect(progress.getByText("succeeded", { exact: true })).toBeVisible();

    await expect(page.getByRole("heading", { name: "Result (2 evaluated / 0 failed of 2)" })).toBeVisible();
    const metric = (label: string) =>
      page.getByText(label, { exact: true }).first().locator("xpath=following-sibling::div[1]");
    await expect(metric("hit_rate")).toHaveText("0.8000");
    await expect(metric("mrr")).toHaveText("0.6667");

    const hit = page.getByRole("row", { name: /What is RAG\?/ });
    const miss = page.getByRole("row", { name: /How do I reset my password\?/ });
    await expect(hit).toContainText("hit");
    await expect(miss).toContainText("miss");
    await page.getByLabel("Only misses and errors").check();
    await expect(hit).toHaveCount(0);
    await expect(miss).toBeVisible();

    const history = page.getByRole("row", { name: /custom/ }).filter({ hasText: "succeeded" });
    await expect(history.first()).toContainText("2 / 0");
  });
});
