import type { SessionSummaryView } from "../types/api";

export type SessionGroupLabel = "Today" | "Yesterday" | "Older";

export interface SessionGroup {
  label: SessionGroupLabel;
  sessions: SessionSummaryView[];
}

function startOfLocalDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function groupSessionsByDate(sessions: SessionSummaryView[]): SessionGroup[] {
  const now = new Date();
  const todayStart = startOfLocalDay(now).getTime();
  const yesterdayStart = todayStart - 86_400_000;

  const groups: Record<SessionGroupLabel, SessionSummaryView[]> = {
    Today: [],
    Yesterday: [],
    Older: [],
  };

  for (const session of sessions) {
    const updated = new Date(session.updated_at).getTime();
    if (updated >= todayStart) {
      groups.Today.push(session);
    } else if (updated >= yesterdayStart) {
      groups.Yesterday.push(session);
    } else {
      groups.Older.push(session);
    }
  }

  return (["Today", "Yesterday", "Older"] as const)
    .filter((label) => groups[label].length > 0)
    .map((label) => ({ label, sessions: groups[label] }));
}

export function filterSessions(
  sessions: SessionSummaryView[],
  query: string,
): SessionSummaryView[] {
  const needle = query.trim().toLowerCase();
  if (!needle) {
    return sessions;
  }
  return sessions.filter((session) => session.title.toLowerCase().includes(needle));
}
