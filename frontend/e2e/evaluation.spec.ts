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

  test("history trend chart plots two runs (WP1)", async ({ page }) => {
    await page.goto("/evaluation");

    // Runs are strictly sequential (single worker); the fake backend sleeps ~0.8s per run.
    for (let i = 0; i < 2; i += 1) {
      await page.getByRole("button", { name: "Run evaluation" }).click();
      await expect(page.getByText("succeeded", { exact: true })).toBeVisible();
    }

    const chart = page.getByRole("img", { name: "Metric trend across recent runs" });
    await expect(chart).toBeVisible();
    // The fake report carries two metrics (hit_rate, mrr): 2 series x 2 runs = 4 points.
    await expect(chart.locator("circle")).toHaveCount(4);
    await expect(page.getByText("last 2 runs (oldest → newest)")).toBeVisible();
  });

  test("missing golden set path is rejected before submitting (WP2)", async ({ page }) => {
    await page.goto("/evaluation");

    await page.getByLabel("Golden test set path").fill("/nope/missing-golden.json");
    await page.getByRole("button", { name: "Run evaluation" }).click();

    await expect(page.getByText(/Test set path does not exist/)).toBeVisible();
    // No task was accepted: the progress section never appears.
    await expect(page.getByRole("heading", { name: "Progress" })).toHaveCount(0);
  });
});
