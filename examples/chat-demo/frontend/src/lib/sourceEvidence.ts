export interface SourceEvidence {
  id: string;
  url: string;
  canonicalUrl: string;
  title?: string;
  domain?: string;
  excerpt?: string;
  sourceType: "assistant_link" | "web_fetch" | "web_search";
  toolCallId?: string;
  publishedAt?: string;
  rank?: number;
}

const MARKDOWN_LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/gi;
const BARE_URL_RE = /(^|[\s(])(https?:\/\/[^\s<>)\]]+)/gi;
const TRAILING_PUNCTUATION_RE = /[.,;:!?]+$/;

function canonicalizeUrl(rawUrl: string): string | undefined {
  try {
    const parsed = new URL(rawUrl.trim().replace(TRAILING_PUNCTUATION_RE, ""));
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return undefined;
    }
    parsed.hash = "";
    if (
      (parsed.protocol === "https:" && parsed.port === "443") ||
      (parsed.protocol === "http:" && parsed.port === "80")
    ) {
      parsed.port = "";
    }
    return parsed.toString();
  } catch {
    return undefined;
  }
}

function domainFromUrl(url: string): string | undefined {
  try {
    return new URL(url).hostname.replace(/^www\./i, "");
  } catch {
    return undefined;
  }
}

function readableTitleFromUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const lastSegment = parsed.pathname
      .split("/")
      .filter(Boolean)
      .at(-1)
      ?.replace(/[-_]+/g, " ")
      .trim();
    return lastSegment || parsed.hostname.replace(/^www\./i, "");
  } catch {
    return url;
  }
}

function buildSource(
  rawUrl: string,
  title: string | undefined,
  rank: number,
): SourceEvidence | undefined {
  const canonicalUrl = canonicalizeUrl(rawUrl);
  if (!canonicalUrl) {
    return undefined;
  }
  return {
    id: `assistant-link-${rank}`,
    url: rawUrl.trim().replace(TRAILING_PUNCTUATION_RE, ""),
    canonicalUrl,
    title: title?.trim() || readableTitleFromUrl(canonicalUrl),
    domain: domainFromUrl(canonicalUrl),
    sourceType: "assistant_link",
    rank,
  };
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function sourceTypeValue(value: unknown): SourceEvidence["sourceType"] | undefined {
  return value === "assistant_link" || value === "web_fetch" || value === "web_search"
    ? value
    : undefined;
}

export function normalizeSourceEvidence(raw: unknown): SourceEvidence | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return undefined;
  }
  const record = raw as Record<string, unknown>;
  const url = stringValue(record.url);
  if (!url) {
    return undefined;
  }
  const canonicalUrl =
    stringValue(record.canonicalUrl) ??
    stringValue(record.canonical_url) ??
    canonicalizeUrl(url);
  if (!canonicalUrl) {
    return undefined;
  }
  const sourceType =
    sourceTypeValue(record.sourceType) ?? sourceTypeValue(record.source_type);
  if (!sourceType) {
    return undefined;
  }
  return {
    id:
      stringValue(record.id) ??
      `${sourceType}-${numberValue(record.rank) ?? 1}-${canonicalUrl}`,
    url,
    canonicalUrl,
    title: stringValue(record.title),
    domain: stringValue(record.domain) ?? domainFromUrl(canonicalUrl),
    excerpt: stringValue(record.excerpt),
    sourceType,
    toolCallId: stringValue(record.toolCallId) ?? stringValue(record.tool_call_id),
    publishedAt: stringValue(record.publishedAt) ?? stringValue(record.published_at),
    rank: numberValue(record.rank),
  };
}

export function normalizeSourceEvidenceList(raw: unknown): SourceEvidence[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw
    .map((item) => normalizeSourceEvidence(item))
    .filter((item): item is SourceEvidence => Boolean(item));
}

export function mergeSourceEvidence(sources: SourceEvidence[]): SourceEvidence[] {
  const priority: Record<SourceEvidence["sourceType"], number> = {
    web_fetch: 0,
    assistant_link: 1,
    web_search: 2,
  };
  const byUrl = new Map<string, SourceEvidence>();

  for (const source of sources) {
    const current = byUrl.get(source.canonicalUrl);
    if (!current) {
      byUrl.set(source.canonicalUrl, source);
      continue;
    }
    const shouldReplace = priority[source.sourceType] < priority[current.sourceType];
    const winner = shouldReplace ? source : current;
    const fallback = shouldReplace ? current : source;
    byUrl.set(source.canonicalUrl, {
      ...winner,
      title: winner.title ?? fallback.title,
      domain: winner.domain ?? fallback.domain,
      excerpt: winner.excerpt ?? fallback.excerpt,
      publishedAt: winner.publishedAt ?? fallback.publishedAt,
      toolCallId: winner.toolCallId ?? fallback.toolCallId,
      rank: winner.rank ?? fallback.rank,
    });
  }

  return Array.from(byUrl.values()).sort((left, right) => {
    const priorityDelta = priority[left.sourceType] - priority[right.sourceType];
    if (priorityDelta !== 0) {
      return priorityDelta;
    }
    return (left.rank ?? 9999) - (right.rank ?? 9999);
  });
}

export function extractAssistantLinkSources(markdown: string): SourceEvidence[] {
  const sources: SourceEvidence[] = [];
  const seen = new Set<string>();
  let rank = 1;

  for (const match of markdown.matchAll(MARKDOWN_LINK_RE)) {
    const source = buildSource(match[2] ?? "", match[1], rank);
    if (!source || seen.has(source.canonicalUrl)) {
      continue;
    }
    seen.add(source.canonicalUrl);
    sources.push(source);
    rank += 1;
  }

  for (const match of markdown.matchAll(BARE_URL_RE)) {
    const source = buildSource(match[2] ?? "", undefined, rank);
    if (!source || seen.has(source.canonicalUrl)) {
      continue;
    }
    seen.add(source.canonicalUrl);
    sources.push(source);
    rank += 1;
  }

  return mergeSourceEvidence(sources);
}
