import { expect, test } from "@playwright/test";
import { resetBackend } from "./helpers";

test.describe("Data Browser", () => {
  test.beforeEach(async ({ page, request }) => {
    await resetBackend(request);
    await page.goto("/data-browser");
    await expect(page.getByRole("row", { name: /Getting Started Guide/ })).toBeVisible();
  });

  test("filters by collection and keyword", async ({ page }) => {
    await expect(page.getByRole("row", { name: /Old Notes/ })).toBeVisible();
    await expect(page.getByText("1-4 of 4")).toBeVisible();

    await page.getByLabel("Collection filter").selectOption("e2e-docs");
    await expect(page.getByRole("row", { name: /Old Notes/ })).toHaveCount(0);
    await expect(page.getByText("1-3 of 3")).toBeVisible();

    await page.getByRole("searchbox", { name: "Search documents" }).fill("faq");
    await expect(page.getByRole("row", { name: /Product FAQ/ })).toBeVisible();
    await expect(page.getByRole("row", { name: /Getting Started Guide/ })).toHaveCount(0);
  });

  test("opens a document with its chunks and image", async ({ page }) => {
    await page.getByRole("row", { name: /Getting Started Guide/ }).click();

    await expect(page.getByRole("heading", { name: "Getting Started Guide" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Chunks (3)" })).toBeVisible();
    await expect(page.getByText("Chunk 1 text of Getting Started Guide.")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Images (1)" })).toBeVisible();
    const image = page.getByRole("img", { name: "img-guide-1" });
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);
    // Server-side file paths never reach the page.
    await expect(page.getByText("/hidden/server/path.png")).toHaveCount(0);
  });

  test("deletes a document only after the confirm step", async ({ page }) => {
    await page.getByRole("row", { name: /Disposable Note/ }).click();
    await expect(page.getByRole("heading", { name: "Disposable Note" })).toBeVisible();

    await page.getByRole("button", { name: "Delete", exact: true }).click();
    const confirm = page.getByRole("alertdialog", { name: "Confirm delete" });
    await expect(confirm).toContainText("delete-me.md");
    await confirm.getByRole("button", { name: "Cancel" }).click();
    await expect(confirm).toBeHidden();
    await expect(page.getByRole("row", { name: /Disposable Note/ })).toBeVisible();

    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await page.getByRole("button", { name: "Confirm delete" }).click();

    await expect(
      page.getByRole("status").filter({ hasText: "Deleted delete-me.md (chroma=2, bm25=2, images=0)." }),
    ).toBeVisible();
    await expect(page.getByRole("row", { name: /Disposable Note/ })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Disposable Note" })).toHaveCount(0);
    await expect(page.getByText("1-3 of 3")).toBeVisible();
  });
});
