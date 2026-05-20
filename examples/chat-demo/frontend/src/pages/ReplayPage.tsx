import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { fetchReplay } from "../lib/api";
import { eventsToMessages, type RunStreamEvent } from "../lib/events";
import { MessageList } from "../components/chat/MessageList";
import { Card, CardContent } from "../components/ui/card";

export function ReplayPage() {
  const params = useParams<{ id: string; runId: string }>();
  const sessionId = params.id ?? "";
  const runId = params.runId ?? "";

  const replay = useQuery({
    queryKey: ["replay", sessionId, runId],
    queryFn: () => fetchReplay(sessionId, runId),
    enabled: Boolean(sessionId && runId),
  });

  const messages =
    replay.data?.events.map(
      (item) => item as unknown as RunStreamEvent<Record<string, unknown>>,
    ) ?? [];
  const rendered = eventsToMessages(messages);

  return (
    <Card className="h-full">
      <CardContent className="space-y-4 p-4">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold">Run replay</h2>
            <p className="text-sm text-muted-foreground">{runId}</p>
          </div>
          <Link
            to={`/sessions/${sessionId}`}
            className="text-sm text-primary underline-offset-4 hover:underline"
          >
            Back to chat
          </Link>
        </div>
        {replay.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading replay...</p>
        ) : null}
        {replay.isError ? (
          <p className="text-sm text-destructive">Failed to load replay.</p>
        ) : null}
        {replay.data ? <MessageList messages={rendered} /> : null}
      </CardContent>
    </Card>
  );
}
