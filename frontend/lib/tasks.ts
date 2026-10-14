import { wsUrl } from "./api";
import { ApiError, toApiError } from "./errors";
import { buildQuery, getJson } from "./queries";
import type { Page } from "./types";

export type TaskStatus = "pending" | "running" | "succeeded" | "failed";

export interface TaskProgress {
  stage: string;
  current: number;
  total: number;
}

export interface TaskErrorInfo {
  type: string;
  message: string;
}

export interface TaskDetail {
  params: Record<string, unknown>;
  progress: TaskProgress | null;
  result: Record<string, unknown> | null;
  error: TaskErrorInfo | null;
}

/**
 * Task snapshot. REST responses carry every field; WS frames only carry
 * `task_id/status/detail/timestamp/version`, so the remaining fields are `null` there.
 */
export interface TaskRecord {
  task_id: string;
  kind: string | null;
  status: TaskStatus;
  version: number;
  detail: TaskDetail;
  created_at: string | null;
  updated_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

type Rec = Record<string, unknown>;

function isRecord(value: unknown): value is Rec {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function finiteNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

const STATUSES: readonly TaskStatus[] = ["pending", "running", "succeeded", "failed"];

export function isTerminalStatus(status: TaskStatus): boolean {
  return status === "succeeded" || status === "failed";
}

export function isActiveStatus(status: TaskStatus): boolean {
  return !isTerminalStatus(status);
}

export function normalizeTaskDetail(raw: unknown): TaskDetail {
  const r = isRecord(raw) ? raw : {};
  const progress = isRecord(r.progress)
    ? {
        stage: str(r.progress.stage) ?? "",
        current: finiteNumber(r.progress.current),
        total: finiteNumber(r.progress.total),
      }
    : null;
  const error = isRecord(r.error)
    ? { type: str(r.error.type) ?? "error", message: str(r.error.message) ?? "Task failed" }
    : null;
  return {
    params: isRecord(r.params) ? r.params : {},
    progress,
    result: isRecord(r.result) ? r.result : null,
    error,
  };
}

/** Normalize either a REST task snapshot or a WS `task_event` frame. */
export function normalizeTask(raw: unknown): TaskRecord {
  const r = isRecord(raw) ? raw : {};
  const status = STATUSES.find((s) => s === r.status) ?? "pending";
  return {
    task_id: str(r.task_id) ?? "",
    kind: str(r.kind),
    status,
    version: finiteNumber(r.version, -1),
    detail: normalizeTaskDetail(r.detail),
    created_at: str(r.created_at),
    updated_at: str(r.updated_at) ?? str(r.timestamp),
    started_at: str(r.started_at),
    finished_at: str(r.finished_at),
  };
}

export function normalizeTaskPage(raw: unknown): Page<TaskRecord> {
  const r = isRecord(raw) ? raw : {};
  const items = Array.isArray(r.items) ? r.items.map(normalizeTask) : [];
  return {
    items,
    total: finiteNumber(r.total, items.length),
    limit: finiteNumber(r.limit, items.length),
    offset: finiteNumber(r.offset),
  };
}

export async function fetchTask(taskId: string, signal?: AbortSignal): Promise<TaskRecord> {
  return normalizeTask(await getJson(`/api/v1/tasks/${encodeURIComponent(taskId)}`, signal));
}

export interface TaskListParams {
  kind?: string;
  status?: TaskStatus;
  limit?: number;
  offset?: number;
}

export async function fetchTasks(
  params: TaskListParams = {},
  signal?: AbortSignal,
): Promise<Page<TaskRecord>> {
  const query = buildQuery({
    kind: params.kind,
    status: params.status,
    limit: params.limit,
    offset: params.offset,
  });
  return normalizeTaskPage(await getJson(`/api/v1/tasks${query}`, signal));
}

/** Percentage (0-100) for a progress record; unknown totals read as 0. */
export function progressPercent(progress: TaskProgress | null): number {
  if (!progress || progress.total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((progress.current / progress.total) * 100)));
}

export type TaskTransport = "websocket" | "polling";

/** Subset of the browser `WebSocket` that `followTask` relies on (injectable for tests). */
export interface SocketLike {
  onopen: ((event?: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onerror: ((event?: unknown) => void) | null;
  onclose: ((event?: unknown) => void) | null;
  close: (code?: number, reason?: string) => void;
}

export interface FollowOptions {
  /** Called once per newer-versioned snapshot, terminal snapshot included. */
  onUpdate: (task: TaskRecord) => void;
  onTransportChange?: (transport: TaskTransport) => void;
  createSocket?: (url: string) => SocketLike;
  fetchSnapshot?: (taskId: string) => Promise<TaskRecord>;
  pollIntervalMs?: number;
  /** A WS that has not opened within this window is abandoned for polling. */
  connectTimeoutMs?: number;
  /** Consecutive poll failures tolerated before `done` rejects. */
  maxPollFailures?: number;
}

export interface TaskFollower {
  /** Resolves with the terminal snapshot, `null` after `stop()`, rejects with `ApiError`. */
  done: Promise<TaskRecord | null>;
  stop: () => void;
}

const DEFAULT_POLL_MS = 1500;
const DEFAULT_CONNECT_TIMEOUT_MS = 4000;
const DEFAULT_MAX_POLL_FAILURES = 5;

/**
 * Follow a background task: WebSocket first (`/api/v1/tasks/{id}/events`), and a transparent
 * fall-back to polling `GET /api/v1/tasks/{id}` when the socket fails, closes before the task
 * is terminal, or never opens. Snapshots are de-duplicated by `version`, so replayed
 * frames and poll results that lag behind the stream are ignored.
 */
export function followTask(taskId: string, options: FollowOptions): TaskFollower {
  const createSocket = options.createSocket ?? ((url: string) => new WebSocket(url) as unknown as SocketLike);
  const fetchSnapshot = options.fetchSnapshot ?? ((id: string) => fetchTask(id));
  const pollMs = options.pollIntervalMs ?? DEFAULT_POLL_MS;
  const connectTimeoutMs = options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS;
  const maxFailures = options.maxPollFailures ?? DEFAULT_MAX_POLL_FAILURES;

  let settled = false;
  let lastVersion = -Infinity;
  let socket: SocketLike | null = null;
  let connectTimer: ReturnType<typeof setTimeout> | undefined;
  let pollTimer: ReturnType<typeof setTimeout> | undefined;
  let polling = false;
  let resolveDone!: (value: TaskRecord | null) => void;
  let rejectDone!: (reason: ApiError) => void;
  const done = new Promise<TaskRecord | null>((resolve, reject) => {
    resolveDone = resolve;
    rejectDone = reject;
  });

  function cleanup(): void {
    settled = true;
    clearTimeout(connectTimer);
    clearTimeout(pollTimer);
    if (socket) {
      const current = socket;
      socket = null;
      current.onopen = current.onmessage = current.onerror = current.onclose = null;
      try {
        current.close();
      } catch {
        // already closed
      }
    }
  }

  function finish(task: TaskRecord): void {
    if (settled) return;
    cleanup();
    resolveDone(task);
  }

  function fail(error: ApiError): void {
    if (settled) return;
    cleanup();
    rejectDone(error);
  }

  function emit(task: TaskRecord): void {
    if (settled || task.version <= lastVersion) return;
    lastVersion = task.version;
    options.onUpdate(task);
    if (isTerminalStatus(task.status)) finish(task);
  }

  function startPolling(): void {
    if (settled || polling) return;
    polling = true;
    clearTimeout(connectTimer);
    if (socket) {
      const current = socket;
      socket = null;
      current.onopen = current.onmessage = current.onerror = current.onclose = null;
      try {
        current.close();
      } catch {
        // already closed
      }
    }
    options.onTransportChange?.("polling");

    let failures = 0;
    const tick = async (): Promise<void> => {
      if (settled) return;
      try {
        const task = await fetchSnapshot(taskId);
        failures = 0;
        emit(task);
      } catch (error) {
        const apiError = toApiError(error);
        failures += 1;
        // 401/404 will not heal by retrying; only transport/5xx noise is retried.
        const fatal = apiError.status === 401 || apiError.status === 404 || apiError.status === 403;
        if (fatal || failures >= maxFailures) {
          fail(apiError);
          return;
        }
      }
      if (!settled) pollTimer = setTimeout(() => void tick(), pollMs);
    };
    void tick();
  }

  function handleMessage(data: unknown): void {
    let frame: unknown;
    try {
      frame = typeof data === "string" ? JSON.parse(data) : data;
    } catch {
      return;
    }
    if (!isRecord(frame)) return;
    if (frame.type === "task_event") {
      emit(normalizeTask(frame));
    } else if (frame.type === "error") {
      // The REST path produces the authoritative error (404/401) or recovers from transient ones.
      startPolling();
    }
  }

  function startSocket(): void {
    let ws: SocketLike;
    try {
      ws = createSocket(wsUrl(`/api/v1/tasks/${encodeURIComponent(taskId)}/events`));
    } catch {
      startPolling();
      return;
    }
    socket = ws;
    options.onTransportChange?.("websocket");
    connectTimer = setTimeout(startPolling, connectTimeoutMs);
    ws.onopen = () => clearTimeout(connectTimer);
    ws.onmessage = (event) => handleMessage(event.data);
    ws.onerror = () => startPolling();
    ws.onclose = () => startPolling();
  }

  startSocket();

  return {
    done,
    stop: () => {
      if (settled) return;
      cleanup();
      resolveDone(null);
    },
  };
}
