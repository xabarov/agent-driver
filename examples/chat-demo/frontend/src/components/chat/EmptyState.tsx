import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

export function EmptyState() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Start a conversation</CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        Send a message and the assistant response will stream in real time from
        the backend SSE endpoint.
      </CardContent>
    </Card>
  );
}
