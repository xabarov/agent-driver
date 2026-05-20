import { useSessions } from "../../lib/sessions";
import { ScrollArea } from "../ui/scroll-area";
import { SessionItem } from "./SessionItem";

export function SessionList() {
  const sessions = useSessions();

  if (sessions.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading sessions...</p>;
  }

  if (sessions.isError) {
    return <p className="text-sm text-destructive">Failed to load sessions.</p>;
  }

  if (!sessions.data || sessions.data.sessions.length === 0) {
    return <p className="text-sm text-muted-foreground">No sessions yet.</p>;
  }

  return (
    <ScrollArea className="h-[calc(100vh-13rem)] rounded-md border">
      <div className="space-y-2 p-2">
        {sessions.data.sessions.map((session) => (
          <SessionItem key={session.session_id} session={session} />
        ))}
      </div>
    </ScrollArea>
  );
}
