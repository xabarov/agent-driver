import { describe, expect, test } from "vitest";

/** Mirrors ChatPage guard: do not reload session while streaming it. */
export function shouldReloadSessionFromQuery(
  store: { streaming: boolean; sessionId?: string },
  sessionId: string,
): boolean {
  if (store.streaming && store.sessionId === sessionId) {
    return false;
  }
  return true;
}

describe("shouldReloadSessionFromQuery", () => {
  test("skips reload while streaming the same session", () => {
    expect(
      shouldReloadSessionFromQuery(
        { streaming: true, sessionId: "session_abc" },
        "session_abc",
      ),
    ).toBe(false);
  });

  test("allows reload when stream finished", () => {
    expect(
      shouldReloadSessionFromQuery(
        { streaming: false, sessionId: "session_abc" },
        "session_abc",
      ),
    ).toBe(true);
  });

  test("allows reload when switching sessions", () => {
    expect(
      shouldReloadSessionFromQuery(
        { streaming: true, sessionId: "session_a" },
        "session_b",
      ),
    ).toBe(true);
  });
});
