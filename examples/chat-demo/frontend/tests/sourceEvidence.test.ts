import { describe, expect, it } from "vitest";

import {
  extractAssistantLinkSources,
  mergeSourceEvidence,
  normalizeSourceEvidenceList,
} from "../src/lib/sourceEvidence";

describe("sourceEvidence", () => {
  it("extracts markdown links and bare urls", () => {
    const sources = extractAssistantLinkSources(
      "Read [Docs](https://example.com/docs#intro) and https://news.example.org/post.",
    );

    expect(sources).toHaveLength(2);
    expect(sources[0]).toMatchObject({
      title: "Docs",
      canonicalUrl: "https://example.com/docs",
      domain: "example.com",
      sourceType: "assistant_link",
    });
    expect(sources[1]).toMatchObject({
      title: "post",
      canonicalUrl: "https://news.example.org/post",
      domain: "news.example.org",
    });
  });

  it("deduplicates canonical urls", () => {
    const sources = extractAssistantLinkSources(
      "[A](https://example.com/a#one) and https://example.com/a#two",
    );

    expect(sources).toHaveLength(1);
    expect(sources[0]?.canonicalUrl).toBe("https://example.com/a");
  });

  it("ignores non-http urls", () => {
    const sources = extractAssistantLinkSources(
      "[mail](mailto:test@example.com) [bad](javascript:alert(1))",
    );

    expect(sources).toEqual([]);
  });

  it("normalizes snake_case source evidence from backend events", () => {
    const sources = normalizeSourceEvidenceList([
      {
        id: "web_fetch:call_1:1",
        url: "https://example.com/article#part",
        canonical_url: "https://example.com/article",
        source_type: "web_fetch",
        tool_call_id: "call_1",
        published_at: "2026-05-30",
        title: "Fetched Article",
        excerpt: "Short quote",
        rank: 1,
      },
    ]);

    expect(sources).toHaveLength(1);
    expect(sources[0]).toMatchObject({
      canonicalUrl: "https://example.com/article",
      sourceType: "web_fetch",
      toolCallId: "call_1",
      publishedAt: "2026-05-30",
      title: "Fetched Article",
      excerpt: "Short quote",
    });
  });

  it("prefers fetched pages over duplicate search hits", () => {
    const sources = mergeSourceEvidence(
      normalizeSourceEvidenceList([
        {
          id: "search",
          url: "https://example.com/a",
          canonical_url: "https://example.com/a",
          source_type: "web_search",
          title: "Search title",
          rank: 1,
        },
        {
          id: "fetch",
          url: "https://example.com/a",
          canonical_url: "https://example.com/a",
          source_type: "web_fetch",
          excerpt: "Fetched excerpt",
          rank: 2,
        },
      ]),
    );

    expect(sources).toHaveLength(1);
    expect(sources[0]).toMatchObject({
      sourceType: "web_fetch",
      title: "Search title",
      excerpt: "Fetched excerpt",
    });
  });
});
