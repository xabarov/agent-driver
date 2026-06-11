import { describe, expect, test } from "vitest";

import {
  eventsToMessages,
  parseSourceLedgerEvent,
  type RunStreamEvent,
} from "../src/lib/events";

function event(
  seq: number,
  name: string,
  data: Record<string, unknown>,
): RunStreamEvent<Record<string, unknown>> {
  return {
    schema_version: "1.0",
    stream_id: `run_1:${seq}`,
    run_id: "run_1",
    attempt_id: "att_1",
    seq,
    event: name,
    source: "runtime_event",
    data,
  };
}

describe("deep research stream events", () => {
  test("normalizes source ledger runtime event", () => {
    const ledger = parseSourceLedgerEvent(
      event(2, "source_ledger_updated", {
        search_candidates: [
          { url: "https://search.example/a", source_type: "web_search" },
        ],
        verified_reads: [
          { url: "https://verified.example/a", source_type: "web_fetch" },
        ],
      }),
    );

    expect(ledger?.searchCandidates[0]?.sourceType).toBe("web_search");
    expect(ledger?.verifiedReads[0]?.domain).toBe("verified.example");
  });

  test("replay messages carry deep research diagnostics", () => {
    const messages = eventsToMessages([
      event(1, "run_started", {}),
      event(2, "token_delta", { delta_text: "Done" }),
      event(3, "source_ledger_updated", {
        verified_reads: [
          { url: "https://verified.example/a", source_type: "web_fetch" },
        ],
      }),
      event(4, "run_completed", {}),
    ]);
    const assistant = messages.find((item) => item.role === "assistant");

    expect(assistant?.role).toBe("assistant");
    if (assistant?.role === "assistant") {
      expect(assistant.deepResearch?.ledger?.verifiedReads).toHaveLength(1);
      expect(assistant.deepResearch?.progress[0]?.event).toBe(
        "source_ledger_updated",
      );
    }
  });

  test("replay messages carry deep research report artifact", () => {
    const messages = eventsToMessages([
      event(1, "run_started", {}),
      event(2, "token_delta", { delta_text: "Done" }),
      event(3, "run_completed", {
        deep_research_artifacts: {
          report_exists: true,
          report_path: "research/report.md",
          report_size_bytes: 2048,
          captured_long_answers: 1,
        },
      }),
    ]);
    const assistant = messages.find((item) => item.role === "assistant");

    expect(assistant?.role).toBe("assistant");
    if (assistant?.role === "assistant") {
      expect(assistant.deepResearch?.artifact?.reportPath).toBe(
        "research/report.md",
      );
      expect(assistant.deepResearch?.artifact?.reportSizeBytes).toBe(2048);
    }
  });

  test("replay messages update report artifact from live artifact event", () => {
    const messages = eventsToMessages([
      event(1, "run_started", {}),
      event(2, "token_delta", { delta_text: "Working" }),
      event(3, "artifact_created", {
        path: "research/report.md",
        kind: "report",
        size_bytes: 512,
      }),
      event(4, "run_completed", {}),
    ]);
    const assistant = messages.find((item) => item.role === "assistant");

    expect(assistant?.role).toBe("assistant");
    if (assistant?.role === "assistant") {
      expect(assistant.deepResearch?.artifact?.reportPath).toBe(
        "research/report.md",
      );
      expect(assistant.deepResearch?.artifact?.reportSizeBytes).toBe(512);
    }
  });
});
