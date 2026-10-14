import type { ChatStage } from "./types";

const LABELS: Record<ChatStage, string> = {
  rewriting: "Rewriting the question",
  retrieving: "Searching the knowledge base",
  generating: "Generating the answer",
  retrying: "Retrying",
};

/** Status-line text: the server's own message wins, otherwise a fixed English label. */
export function stageLabel(stage: ChatStage | null | undefined, serverMessage?: string): string | null {
  if (!stage) return null;
  const message = serverMessage?.trim();
  return message ? message : `${LABELS[stage]}...`;
}

export const STAGE_ORDER: readonly ChatStage[] = ["rewriting", "retrieving", "generating"];
