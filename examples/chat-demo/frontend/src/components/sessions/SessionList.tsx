import { useMemo, useState } from "react";
import { Search } from "lucide-react";

import { filterSessions, groupSessionsByDate } from "../../lib/sessionGroups";
import { useSessions } from "../../lib/sessions";
import { ScrollArea } from "../ui/scroll-area";
import { SessionItem } from "./SessionItem";

export function SessionList() {
  const [search, setSearch] = useState("");
  const sessions = useSessions();

  const grouped = useMemo(() => {
    if (!sessions.data?.sessions) {
      return [];
    }
    const filtered = filterSessions(sessions.data.sessions, search);
    return groupSessionsByDate(filtered);
  }, [search, sessions.data?.sessions]);

  if (sessions.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading sessions…</p>;
  }

  if (sessions.isError) {
    return <p className="text-sm text-destructive">Failed to load sessions.</p>;
  }

  if (!sessions.data || sessions.data.sessions.length === 0) {
    return <p className="text-sm text-muted-foreground">No sessions yet.</p>;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center gap-2 rounded-md border border-border bg-background/60 px-2 py-1.5">
        <Search className="h-3.5 w-3.5 text-muted-foreground" />
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search sessions…"
          className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
      <ScrollArea className="min-h-0 flex-1 overflow-x-hidden">
        <div className="w-full min-w-0 space-y-3 p-1">
          {grouped.length === 0 ? (
            <p className="text-xs text-muted-foreground">No matching sessions.</p>
          ) : null}
          {grouped.map((group) => (
            <div key={group.label} className="space-y-1">
              <p className="px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {group.label}
              </p>
              {group.sessions.map((session) => (
                <SessionItem key={session.session_id} session={session} />
              ))}
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
