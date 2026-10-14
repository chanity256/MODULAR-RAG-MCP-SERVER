import { getJson } from "./queries";
import { requestJson } from "./data";

type Rec = Record<string, unknown>;

function isRecord(value: unknown): value is Rec {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export const SEARCH_MODES = ["normal", "fast", "deep"] as const;
export type SearchMode = (typeof SEARCH_MODES)[number];

export const MODE_LABELS: Record<SearchMode, string> = { normal: "Normal", fast: "Fast", deep: "Deep" };

export interface ModeStatus {
  available: boolean;
  reason: string | null;
}

export interface SearchCapabilities {
  modes: SearchMode[];
  default_mode: SearchMode;
  agentic_enabled: boolean;
  allow_deep: boolean;
  availability: Record<SearchMode, ModeStatus>;
}

export interface SearchCitation {
  id: number | string | null;
  source: string;
  page: number | null;
  score: number | null;
  text: string;
}

export interface TelemetryRow {
  key: string;
  value: string;
}

export interface AgenticMeta {
  mode: string | null;
  total_llm_tokens: number | null;
  llm_call_count: number | null;
  elapsed_ms: number | null;
  evidence_files: string[];
  agentic_answer: string | null;
  telemetry: TelemetryRow[];
}

export interface SearchResult {
  mode: SearchMode;
  query: string;
  elapsed_ms: number;
  markdown: string;
  citations: SearchCitation[];
  agentic: AgenticMeta | null;
  is_error: boolean;
  error: string | null;
}

export interface SearchFormValues {
  mode: SearchMode;
  query: string;
  /** Raw text of the Top-K field; empty means the server default. */
  topK: string;
  collection: string;
}

export interface SearchRequestBody {
  mode: SearchMode;
  query: string;
  top_k?: number;
  collection?: string;
}

function isMode(value: unknown): value is SearchMode {
  return typeof value === "string" && (SEARCH_MODES as readonly string[]).includes(value);
}

/** Returns an error message when the form cannot be submitted. */
export function validateSearchForm(values: SearchFormValues): string | null {
  if (!values.query.trim()) return "Enter a query first.";
  const raw = values.topK.trim();
  if (raw !== "") {
    if (!/^\d+$/.test(raw)) return "Top-K must be an integer between 1 and 100.";
    const n = Number.parseInt(raw, 10);
    if (n < 1 || n > 100) return "Top-K must be an integer between 1 and 100.";
  }
  return null;
}

/** Only the four fields the API accepts (extras are 422); collection applies to normal mode only. */
export function buildSearchRequest(values: SearchFormValues): SearchRequestBody {
  const body: SearchRequestBody = { mode: values.mode, query: values.query.trim() };
  const raw = values.topK.trim();
  if (raw !== "") body.top_k = Number.parseInt(raw, 10);
  const collection = values.collection.trim();
  if (values.mode === "normal" && collection) body.collection = collection;
  return body;
}

export function normalizeCapabilities(raw: unknown): SearchCapabilities {
  const r = isRecord(raw) ? raw : {};
  const rawAvailability = isRecord(r.availability) ? r.availability : {};
  const modes = Array.isArray(r.modes) ? r.modes.filter(isMode) : [];
  const availability = {} as Record<SearchMode, ModeStatus>;
  for (const mode of SEARCH_MODES) {
    const entry = rawAvailability[mode];
    if (isRecord(entry)) {
      availability[mode] = { available: entry.available === true, reason: str(entry.reason) };
    } else {
      availability[mode] = { available: mode === "normal" || modes.includes(mode), reason: null };
    }
  }
  return {
    modes,
    default_mode: isMode(r.default_mode) ? r.default_mode : "normal",
    agentic_enabled: r.agentic_enabled === true,
    allow_deep: r.allow_deep === true,
    availability,
  };
}

/** The mode to preselect: the server default when usable, otherwise normal. */
export function initialMode(capabilities: SearchCapabilities): SearchMode {
  return capabilities.availability[capabilities.default_mode].available ? capabilities.default_mode : "normal";
}

export function isModeAvailable(capabilities: SearchCapabilities | undefined, mode: SearchMode): boolean {
  if (!capabilities) return mode === "normal";
  return capabilities.availability[mode].available;
}

export function normalizeCitations(raw: unknown): SearchCitation[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(isRecord).map((c) => ({
    id: typeof c.id === "number" || typeof c.id === "string" ? c.id : null,
    source: str(c.source) ?? "",
    page: num(c.page),
    score: num(c.score),
    text: str(c.text) ?? "",
  }));
}

function telemetryValue(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (isRecord(value)) {
    const parts = Object.entries(value).map(([k, v]) => `${k}: ${telemetryValue(v)}`);
    return parts.length > 0 ? parts.join(", ") : "(none)";
  }
  if (Array.isArray(value)) return value.map(telemetryValue).join(", ");
  return String(value);
}

/** Flatten a telemetry object into key/value rows; anything but an object yields no rows. */
export function telemetryRows(telemetry: unknown): TelemetryRow[] {
  if (!isRecord(telemetry)) return [];
  return Object.entries(telemetry).map(([key, value]) => ({ key, value: telemetryValue(value) }));
}

export function normalizeAgentic(raw: unknown): AgenticMeta | null {
  if (!isRecord(raw)) return null;
  return {
    mode: str(raw.mode),
    total_llm_tokens: num(raw.total_llm_tokens),
    llm_call_count: num(raw.llm_call_count),
    elapsed_ms: num(raw.elapsed_ms),
    evidence_files: Array.isArray(raw.evidence_files) ? raw.evidence_files.map(String) : [],
    agentic_answer: str(raw.agentic_answer),
    telemetry: telemetryRows(raw.telemetry),
  };
}

export function normalizeSearchResponse(raw: unknown): SearchResult {
  const r = isRecord(raw) ? raw : {};
  const structured = isRecord(r.structured) ? r.structured : {};
  const isError = r.is_error === true;
  return {
    mode: isMode(r.mode) ? r.mode : "normal",
    query: str(r.query) ?? "",
    elapsed_ms: num(r.elapsed_ms) ?? 0,
    markdown: str(r.markdown) ?? "",
    citations: normalizeCitations(structured.citations),
    agentic: normalizeAgentic(structured.agentic),
    is_error: isError,
    error: isError ? (str(r.error) ?? str(r.markdown) ?? "Unknown error") : null,
  };
}

export async function fetchSearchCapabilities(signal?: AbortSignal): Promise<SearchCapabilities> {
  return normalizeCapabilities(await getJson("/api/v1/search/capabilities", signal));
}

/** Search failures arrive as HTTP 200 with `is_error`; 403/422/401 throw `ApiError`. */
export async function runSearch(values: SearchFormValues, signal?: AbortSignal): Promise<SearchResult> {
  return normalizeSearchResponse(await requestJson("POST", "/api/v1/search", null, { json: buildSearchRequest(values), signal }));
}

export function formatScore(score: number | null): string {
  return score === null ? "-" : score.toFixed(4);
}

export function formatElapsed(ms: number | null): string {
  return ms === null ? "-" : `${Math.round(ms)} ms`;
}
