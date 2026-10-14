import { expect, test } from "@playwright/test";
import { resetBackend } from "./helpers";

test.describe("Ingestion Manager", () => {
  test.beforeEach(async ({ page, request }) => {
    await resetBackend(request);
    await page.goto("/ingestion-manager");
    await expect(page.getByRole("heading", { name: "Ingestion Manager" })).toBeVisible();
  });

  test("asks for a file before submitting", async ({ page }) => {
    await page.getByRole("button", { name: "Start ingestion" }).click();
    await expect(page.getByText("Select at least one file first.")).toBeVisible();
  });

  test("uploads a file, follows the task live and shows the summary", async ({ page }) => {
    await page.getByLabel("Files to ingest").setInputFiles({
      name: "e2e-upload.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# Uploaded\n\nA tiny document for the ingestion flow.\n"),
    });
    await expect(page.getByText("e2e-upload.md").first()).toBeVisible();
    await page.getByLabel("Target collection").selectOption("e2e-docs");
    await page.getByRole("button", { name: "Start ingestion" }).click();

    const progress = page.locator("section").filter({ has: page.getByRole("heading", { name: "Progress" }) });
    await expect(progress).toBeVisible();
    // Task events travel over the WebSocket (polling is only the fallback).
    await expect(progress.getByText("live (WebSocket)")).toBeVisible();
    await expect(progress.getByText("succeeded", { exact: true })).toBeVisible();
    const bar = progress.getByRole("progressbar");
    await expect(bar).toHaveAttribute("aria-valuenow", "100");
    await expect(progress.getByText("Completed")).toBeVisible();

    const files = progress.getByText("Files", { exact: true }).locator("xpath=following-sibling::div[1]");
    await expect(files).toHaveText("1");
    await expect(progress.getByText("collection: e2e-docs")).toBeVisible();
    await expect(progress.getByText("Chunks", { exact: true }).locator("xpath=following-sibling::div[1]")).toHaveText("3");
    await expect(progress.getByText("Failed", { exact: true }).locator("xpath=following-sibling::div[1]")).toHaveText("0");

    const recent = page.getByRole("row", { name: /e2e-upload\.md → e2e-docs/ }).first();
    await expect(recent).toContainText("succeeded");

    const managed = page.getByRole("listitem").filter({ hasText: "e2e-upload.md" }).first();
    await expect(managed).toContainText("e2e-docs · 3 chunks / 0 images");
  });
});
