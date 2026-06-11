import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { fetchReplay, fetchRunTraceSummary } from "../lib/api";
import {
  eventsToMessages,
  parseSteeringEvents,
  type RunStreamEvent,
} from "../lib/events";
import { MessageList } from "../components/chat/MessageList";
import { Badge } from "../components/ui/badge";
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
  const traceSummary = useQuery({
    queryKey: ["trace-summary", runId],
    queryFn: () => fetchRunTraceSummary(runId),
    enabled: Boolean(runId),
  });

  const messages =
    replay.data?.events.map(
      (item) => item as unknown as RunStreamEvent<Record<string, unknown>>,
    ) ?? [];
  const rendered = eventsToMessages(messages);
  const steeringEvents = parseSteeringEvents(messages);
  const compaction = traceSummary.data?.compaction;
  const compactionAttempts = compaction?.attempts ?? 0;

  return (
    <Card className="h-full rounded-lg">
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
        {steeringEvents.length > 0 ? (
          <div className="rounded-lg border border-border bg-muted/30 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="text-sm font-medium">Steering timeline</h3>
              <Badge variant="outline">{steeringEvents.length}</Badge>
            </div>
            <div className="space-y-1.5">
              {steeringEvents.map((event) => (
                <div
                  key={`${event.seq}:${event.event}:${event.queueId}`}
                  className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"
                >
                  <span className="font-mono text-foreground">#{event.seq}</span>
                  <Badge variant="secondary" className="rounded-md">
                    {event.event}
                  </Badge>
                  {event.kind ? <span>{event.kind}</span> : null}
                  {event.priority ? <span>{event.priority}</span> : null}
                  <span className="font-mono">{event.queueId}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
        {compaction && compactionAttempts > 0 ? (
          <div className="rounded-lg border border-border bg-muted/30 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="text-sm font-medium">Compaction summary</h3>
              <Badge variant="outline">{compactionAttempts} attempts</Badge>
            </div>
            <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
              <Badge variant="secondary">started {compaction.started ?? 0}</Badge>
              <Badge variant="secondary">success {compaction.successful ?? 0}</Badge>
              <Badge variant="secondary">failed {compaction.failed ?? 0}</Badge>
              <Badge variant="secondary">skipped {compaction.skipped ?? 0}</Badge>
              {compaction.modes?.map((mode) => (
                <Badge key={mode} variant="outline">
                  {mode}
                </Badge>
              ))}
              {compaction.circuit_breaker_open ? (
                <Badge variant="secondary" className="bg-amber-500/15 text-amber-700">
                  circuit breaker
                </Badge>
              ) : null}
            </div>
          </div>
        ) : null}
        {replay.data ? <MessageList messages={rendered} /> : null}
      </CardContent>
    </Card>
  );
}
