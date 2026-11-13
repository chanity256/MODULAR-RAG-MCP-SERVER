import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";

/** Restore the fake backend's seeded documents (traces, sessions and tasks are append-only). */
export async function resetBackend(request: APIRequestContext): Promise<void> {
  const response = await request.post("/__e2e__/reset");
  expect(response.ok()).toBeTruthy();
}

export function chatRegion(page: Page): Locator {
  return page.getByRole("region", { name: "Chat", exact: true });
}

export function sessionList(page: Page): Locator {
  return page.getByRole("complementary", { name: "Chat sessions" });
}

/**
 * Sessions are never reset, so a retried attempt must not collide with the titles its failed
 * predecessor left behind. Stays under the 40-char session-title cut-off.
 */
export function uniq(label: string): string {
  return `${label} #${Math.random().toString(36).slice(2, 6)}`;
}

/** The session entry button for a title (exact: the sibling delete button's label embeds it). */
export function sessionRow(page: Page, title: string): Locator {
  return sessionList(page).getByRole("button", { name: title, exact: true });
}

/** Type into the composer and press Send; waits until the user bubble is rendered. */
export async function sendChat(page: Page, text: string): Promise<void> {
  await page.getByRole("textbox", { name: "Message" }).fill(text);
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(chatRegion(page).getByText(text, { exact: true })).toBeVisible();
}
