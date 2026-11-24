import { expect, test, type Locator } from "@playwright/test";
import { chatRegion, sendChat, sessionList, sessionRow, uniq } from "./helpers";

const ANSWER = "grounds answers in your documents";

// The chip is a button inside <sup> whose line-height is 0, so its box has zero height: a real
// pointer lands on the glyph, but Playwright cannot scroll to or target a zero-size element.
async function clickMarker(marker: Locator): Promise<void> {
  await marker.dispatchEvent("click");
}

test.describe("Chat", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Ask your knowledge base" })).toBeVisible();
  });

  test("streams a cited answer, shows sources and lists the new session", async ({ page }) => {
    const question = uniq("What is RAG? (main flow)");
    const chat = chatRegion(page);
    await sendChat(page, question);

    const answer = chat.locator(".chat-md").last();
    await expect(answer).toContainText(ANSWER);
    await expect(chat.getByRole("button", { name: "Stop generating" })).toBeHidden();

    // Fully grounded answer: no warning banner, usage line from the final frame.
    await expect(chat.getByTestId("ungrounded-banner")).toHaveCount(0);
    await expect(chat.getByText("150 tokens (in 120 / out 30)")).toBeVisible();

    // The [1] marker becomes a chip; clicking it opens the sources panel with the score.
    const marker = chat.getByTitle("guide.md p.2");
    await expect(marker).toHaveText("1");
    await clickMarker(marker);
    const sources = chat.getByRole("region", { name: "Sources" });
    await expect(sources.getByRole("button", { name: "Sources (1)" })).toHaveAttribute("aria-expanded", "true");
    const entry = sources.getByRole("listitem");
    await expect(entry).toContainText("guide.md p.2");
    await expect(entry).toContainText("score 0.873");
    await expect(entry).toContainText("chunk-guide-1");
    await expect(entry).toHaveAttribute("data-highlighted", "true");

    await expect(sessionRow(page, question)).toBeVisible();
  });

  test("switches retrieval mode; deep stays visible but disabled", async ({ page }) => {
    const chat = chatRegion(page);
    const modes = chat.getByRole("radiogroup", { name: "Retrieval mode" });
    await expect(modes.getByRole("radio", { name: "Hybrid" })).toHaveAttribute("aria-checked", "true");

    const deep = modes.getByRole("radio", { name: /Agentic \(deep\)/ });
    await expect(deep).toBeDisabled();
    await expect(deep).toHaveAttribute("title", /allow_deep/);

    const fast = modes.getByRole("radio", { name: "Agentic (fast)" });
    await fast.click();
    await expect(fast).toHaveAttribute("aria-checked", "true");
    await expect(modes.getByRole("radio", { name: "Hybrid" })).toHaveAttribute("aria-checked", "false");

    await sendChat(page, uniq("Ask the sidecar (mode flow)"));
    await expect(chat.locator(".chat-md").last()).toContainText("Agentic fast answer from the sidecar");
    await clickMarker(chat.getByTitle("agentic-notes.md"));
    await expect(chat.getByRole("region", { name: "Sources" }).getByRole("listitem")).toContainText(
      "agentic-notes.md",
    );
  });

  // PAGEINDEX_TREE_RETRIEVAL_SPEC PI6: the fake backend serves longdoc through a fake tree
  // backend, so the option appears alongside the agentic modes and its citations carry a
  // section path and a 1-based line range (rendered by citationLabel).
  test("longdoc mode answers with section and line citations", async ({ page }) => {
    const chat = chatRegion(page);
    const modes = chat.getByRole("radiogroup", { name: "Retrieval mode" });
    const longdoc = modes.getByRole("radio", { name: "Longdoc" });
    await expect(longdoc).toBeVisible();
    await expect(modes.getByRole("radio", { name: "Hybrid" })).toHaveCount(1);
    await longdoc.click();
    await expect(longdoc).toHaveAttribute("aria-checked", "true");

    await sendChat(page, uniq("Locate the section (longdoc flow)"));
    await expect(chat.locator(".chat-md").last()).toContainText(ANSWER);

    const label = "longdoc-notes.md · 2 RAG 架构 > 2.1 模块化设计 · L12–45";
    await clickMarker(chat.getByTitle(label));
    const entry = chat.getByRole("region", { name: "Sources" }).getByRole("listitem");
    await expect(entry).toContainText("longdoc-notes.md");
    await expect(entry).toContainText("2 RAG 架构 > 2.1 模块化设计");
    await expect(entry).toContainText("L12–45");
  });

  // MODE_ROUTER_SPEC MR5: the fake backend serves a scripted mode router, so the Auto
  // option appears and the decision (or fallback) is reported on the assistant message.
  test("auto mode routes the question and reports the decision", async ({ page }) => {
    const chat = chatRegion(page);
    const modes = chat.getByRole("radiogroup", { name: "Retrieval mode" });
    const auto = modes.getByRole("radio", { name: "Auto" });
    await expect(auto).toBeVisible();
    await auto.click();
    await expect(auto).toHaveAttribute("aria-checked", "true");

    // Unmarked question routes to hybrid; the bubble meta line reports the decision.
    await sendChat(page, uniq("What is RAG? (auto flow)"));
    await expect(chat.getByText("Auto → Hybrid", { exact: true })).toBeVisible();

    // ROUTE_FAST keyword routes to agentic_fast.
    await sendChat(page, uniq("ROUTE_FAST scan the notes (auto flow)"));
    await expect(chat.getByText("Auto → Agentic (fast)", { exact: true })).toBeVisible();

    // FORCE_ROUTING_FALLBACK makes the router unreachable; the turn falls back to hybrid.
    await sendChat(page, uniq("FORCE_ROUTING_FALLBACK please (auto flow)"));
    await expect(chat.getByText("Auto → Hybrid (fell back: router unreachable)", { exact: true })).toBeVisible();
  });

  test("reload keeps the session list and reopening restores the history", async ({ page }) => {
    const question = uniq("Remember me (reload flow)");
    const chat = chatRegion(page);
    await sendChat(page, question);
    await expect(chat.locator(".chat-md").last()).toContainText(ANSWER);
    const row = sessionRow(page, question);
    await expect(row).toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { name: "Ask your knowledge base" })).toBeVisible();
    await expect(row).toBeVisible();

    await row.click();
    await expect(chat.getByText(question, { exact: true })).toBeVisible();
    await expect(chat.locator(".chat-md").last()).toContainText(ANSWER);
    await expect(chat.getByRole("button", { name: "Sources (1)" })).toBeVisible();
    await expect(row).toHaveAttribute("aria-current", "true");

    await page.getByRole("button", { name: "New chat" }).click();
    await expect(page.getByRole("heading", { name: "Ask your knowledge base" })).toBeVisible();
    await expect(row).toBeVisible();
  });

  test("deletes a session after confirmation", async ({ page }) => {
    const question = uniq("Delete this chat (session flow)");
    await sendChat(page, question);
    await expect(chatRegion(page).locator(".chat-md").last()).toContainText(ANSWER);
    const list = sessionList(page);
    await expect(sessionRow(page, question)).toBeVisible();

    page.once("dialog", (dialog) => dialog.accept());
    await list.getByRole("button", { name: `Delete chat ${question}` }).click();

    await expect(sessionRow(page, question)).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Ask your knowledge base" })).toBeVisible();
  });

  test("stops a slow answer and keeps the partial text", async ({ page }) => {
    const question = uniq("SLOW answer (cancel flow)");
    const chat = chatRegion(page);
    await sendChat(page, question);

    const stop = chat.getByRole("button", { name: "Stop generating" });
    await expect(stop).toBeVisible();
    await expect(chat.locator(".chat-md").last()).toContainText("slow-0");
    await stop.click();

    await expect(chat.getByText("Stopped. The answer above is partial.")).toBeVisible();
    await expect(chat.getByRole("button", { name: "Send message" })).toBeVisible();
    await expect(chat.locator(".chat-md").last()).toContainText("slow-0");
    // The stream was cut well before its ~10 s end.
    await expect(chat.locator(".chat-md").last()).not.toContainText("slow-40");
  });

  test("shows the error frame with a retry action", async ({ page }) => {
    const chat = chatRegion(page);
    await sendChat(page, uniq("FORCE_ERROR please (error flow)"));

    const alert = chat.getByRole("alert").filter({ hasText: "Model provider error" });
    await expect(alert).toBeVisible();
    await expect(alert).toContainText("UPSTREAM");
    await expect(alert.getByRole("button", { name: "Retry last message" })).toBeVisible();
    // No empty assistant bubble is left behind.
    await expect(chat.locator(".chat-md")).toHaveCount(0);

    await alert.getByRole("button", { name: "Dismiss error" }).click();
    await expect(alert).toBeHidden();
    await expect(chat.getByRole("button", { name: "Send message" })).toBeVisible();
  });
});
