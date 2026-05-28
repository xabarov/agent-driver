/**
 * Verdict from `agent_driver.runtime.planning_check` (surfaced by backend
 * `aggregate_metadata_from_events`). Absent when the assistant turn never
 * touched a planning tool — i.e. the agent wasn't in plan mode at all.
 *
 *  - `"engaged"`    — planning ran AND a data tool ran. The plan was
 *                     actually executed; no warning needed.
 *  - `"fabricated"` — planning ran but no data tool ran. The model wrote
 *                     a plan and then a prose answer without invoking any
 *                     of it. UI should warn the user.
 */
export type PlanningExecutedVerdict = "engaged" | "fabricated";

export interface AssistantMessageMetadata {
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  durationMs?: number;
  tokensPerSecond?: number;
  costUsd?: number;
  model?: string;
  provider?: string;
  estimated?: boolean;
  planningExecuted?: PlanningExecutedVerdict;
}

export interface LlmCompletedPatch {
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  durationMs?: number;
  costUsd?: number;
  model?: string;
  provider?: string;
  estimated?: boolean;
}

function asInt(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.max(0, Math.floor(value));
  }
  return undefined;
}

function asFloat(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return undefined;
}

function parseUsageDict(usage: Record<string, unknown>): LlmCompletedPatch {
  const prompt = asInt(usage.input_tokens ?? usage.prompt_tokens);
  const completion = asInt(usage.output_tokens ?? usage.completion_tokens);
  let total = asInt(usage.total_tokens);
  if (total === undefined && (prompt !== undefined || completion !== undefined)) {
    total = (prompt ?? 0) + (completion ?? 0);
  }
  let costUsd = asFloat(usage.cost_usd_estimate);
  if (costUsd === undefined) {
    for (const key of ["total_cost", "cost", "generation_cost"]) {
      costUsd = asFloat(usage[key]);
      if (costUsd !== undefined) {
        break;
      }
    }
  }
  const patch: LlmCompletedPatch = { estimated: true };
  if (prompt !== undefined) {
    patch.promptTokens = prompt;
  }
  if (completion !== undefined) {
    patch.completionTokens = completion;
  }
  if (total !== undefined) {
    patch.totalTokens = total;
  }
  if (costUsd !== undefined) {
    patch.costUsd = costUsd;
  }
  const model = usage.model_name;
  if (typeof model === "string" && model) {
    patch.model = model;
  }
  const provider = usage.model_provider;
  if (typeof provider === "string" && provider) {
    patch.provider = provider;
  }
  return patch;
}

export function parseLlmCompletedData(data: Record<string, unknown>): LlmCompletedPatch {
  const patch: LlmCompletedPatch = {};
  const usage = data.usage;
  if (usage && typeof usage === "object") {
    Object.assign(patch, parseUsageDict(usage as Record<string, unknown>));
  }
  const durationMs = asFloat(data.duration_ms);
  if (durationMs !== undefined) {
    patch.durationMs = durationMs;
  }
  if (typeof data.model === "string" && data.model) {
    patch.model = data.model;
  }
  if (typeof data.provider === "string" && data.provider) {
    patch.provider = data.provider;
  }
  return patch;
}

export function mergeAssistantMetadata(
  previous: AssistantMessageMetadata | undefined,
  patch: LlmCompletedPatch,
): AssistantMessageMetadata {
  const base: AssistantMessageMetadata = { ...(previous ?? {}) };
  if (patch.promptTokens !== undefined) {
    base.promptTokens = (base.promptTokens ?? 0) + patch.promptTokens;
  }
  if (patch.completionTokens !== undefined) {
    base.completionTokens = (base.completionTokens ?? 0) + patch.completionTokens;
  }
  if (patch.totalTokens !== undefined) {
    base.totalTokens = (base.totalTokens ?? 0) + patch.totalTokens;
  } else if (base.promptTokens !== undefined || base.completionTokens !== undefined) {
    base.totalTokens = (base.promptTokens ?? 0) + (base.completionTokens ?? 0);
  }
  if (patch.durationMs !== undefined) {
    base.durationMs = (base.durationMs ?? 0) + patch.durationMs;
  }
  if (patch.costUsd !== undefined) {
    base.costUsd = (base.costUsd ?? 0) + patch.costUsd;
  }
  if (patch.model) {
    base.model = patch.model;
  }
  if (patch.provider) {
    base.provider = patch.provider;
  }
  base.estimated = patch.estimated ?? base.estimated ?? true;
  base.tokensPerSecond = computeTokensPerSecond(base);
  return base;
}

export function computeTokensPerSecond(
  metadata: AssistantMessageMetadata,
): number | undefined {
  const completion = metadata.completionTokens ?? 0;
  const durationMs = metadata.durationMs ?? 0;
  if (completion <= 0 || durationMs <= 0) {
    return undefined;
  }
  return completion / (durationMs / 1000);
}

export function formatTokensPerSecond(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const rounded = value >= 100 ? value.toFixed(1) : value.toFixed(1);
  return `~${rounded} tokens/s`;
}

export function formatTokenCount(metadata: AssistantMessageMetadata): string {
  const total = metadata.totalTokens;
  if (total === undefined || total <= 0) {
    return "—";
  }
  return `${total} tokens`;
}

export function formatCostUsd(costUsd: number | undefined): string {
  if (costUsd === undefined || !Number.isFinite(costUsd)) {
    return "—";
  }
  return `$${costUsd.toFixed(7)}`;
}

export function formatDurationSec(durationMs: number | undefined): string {
  if (durationMs === undefined || durationMs <= 0) {
    return "—";
  }
  return `${(durationMs / 1000).toFixed(1)}s`;
}

export function normalizeMetadataFromApi(
  raw: Record<string, unknown> | AssistantMessageMetadata | undefined | null,
): AssistantMessageMetadata | undefined {
  if (!raw || typeof raw !== "object") {
    return undefined;
  }
  const patch: LlmCompletedPatch = {};
  const record = raw as Record<string, unknown>;
  const meta = raw as AssistantMessageMetadata;
  const prompt = asInt(
    meta.promptTokens ?? record.promptTokens ?? record.prompt_tokens ?? record.input_tokens,
  );
  const completion = asInt(
    meta.completionTokens ??
      record.completionTokens ??
      record.completion_tokens ??
      record.output_tokens,
  );
  const total = asInt(meta.totalTokens ?? record.totalTokens ?? record.total_tokens);
  const durationMs = asFloat(meta.durationMs ?? record.durationMs ?? record.duration_ms);
  const costUsd = asFloat(meta.costUsd ?? record.costUsd ?? record.cost_usd_estimate);
  if (prompt !== undefined) {
    patch.promptTokens = prompt;
  }
  if (completion !== undefined) {
    patch.completionTokens = completion;
  }
  if (total !== undefined) {
    patch.totalTokens = total;
  }
  if (durationMs !== undefined) {
    patch.durationMs = durationMs;
  }
  if (costUsd !== undefined) {
    patch.costUsd = costUsd;
  }
  if (typeof raw.model === "string") {
    patch.model = raw.model;
  }
  if (typeof raw.provider === "string") {
    patch.provider = raw.provider;
  }
  if (Object.keys(patch).length === 0) {
    return undefined;
  }
  patch.estimated = true;
  return mergeAssistantMetadata(undefined, patch);
}

export function pickMetadata(
  server: AssistantMessageMetadata | undefined,
  local: AssistantMessageMetadata | undefined,
): AssistantMessageMetadata | undefined {
  if (server && hasMetadataContent(server)) {
    return server;
  }
  if (local && hasMetadataContent(local)) {
    return local;
  }
  return server ?? local;
}

export function hasMetadataContent(metadata: AssistantMessageMetadata | undefined): boolean {
  if (!metadata) {
    return false;
  }
  return (
    (metadata.totalTokens ?? 0) > 0 ||
    (metadata.completionTokens ?? 0) > 0 ||
    (metadata.durationMs ?? 0) > 0 ||
    metadata.costUsd !== undefined
  );
}
