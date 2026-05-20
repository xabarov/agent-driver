import { Card } from "../ui/card";
import { NewSessionButton } from "./NewSessionButton";
import { SessionList } from "./SessionList";

export function Sidebar() {
  return (
    <Card className="sticky top-4 flex h-[calc(100vh-2rem)] flex-col gap-4 p-3">
      <div className="space-y-1">
        <h2 className="text-sm font-semibold">Sessions</h2>
        <p className="text-xs text-muted-foreground">Your persisted chat history</p>
      </div>
      <NewSessionButton />
      <SessionList />
    </Card>
  );
}
