import { expect, test } from "@playwright/test";
import { AUTH_TOKEN } from "./constants";
import { chatRegion, sendChat, sessionRow, uniq } from "./helpers";

// Runs against the second fake server, which requires `Authorization: Bearer <token>`.
test.describe("API token flow", () => {
  test("chat asks for a token on 401, rejects a wrong one, then works over REST and WS", async ({ page }) => {
    await page.goto("/");
    const chat = chatRegion(page);
    const notice = chat.getByRole("alert").filter({ hasText: "The API requires a token" });
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("HTTP 401");

    await notice.getByLabel("API token").fill("wrong-token");
    await notice.getByRole("button", { name: "Save and retry" }).click();
    await expect(chat.getByRole("alert").filter({ hasText: "The API requires a token" })).toBeVisible();

    await chat.getByLabel("API token").fill(AUTH_TOKEN);
    await chat.getByRole("button", { name: "Save and retry" }).click();
    await expect(chat.getByRole("alert").filter({ hasText: "The API requires a token" })).toHaveCount(0);

    const question = uniq("Authenticated question (auth)");
    await sendChat(page, question);
    await expect(chat.locator(".chat-md").last()).toContainText("grounds answers in your documents");

    // The token lives in localStorage, so a reload stays authenticated.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Ask your knowledge base" })).toBeVisible();
    await expect(chat.getByRole("alert").filter({ hasText: "The API requires a token" })).toHaveCount(0);
    await expect(sessionRow(page, question)).toBeVisible();
  });

  test("dashboard pages ask for a token and load after it is saved", async ({ page }) => {
    await page.goto("/overview");
    const notice = page.getByRole("alert").filter({ hasText: "The API requires a token" });
    await expect(notice).toBeVisible();

    await notice.getByLabel("API token").fill(AUTH_TOKEN);
    await notice.getByRole("button", { name: "Save and retry" }).click();

    await expect(notice).toHaveCount(0);
    await expect(page.getByText("Agentic Search", { exact: true }).first()).toBeVisible();
  });
});
