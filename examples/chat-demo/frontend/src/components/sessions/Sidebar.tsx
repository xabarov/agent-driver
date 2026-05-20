import { Card } from "../ui/card";
import { NewSessionButton } from "./NewSessionButton";
import { SessionList } from "./SessionList";

export function Sidebar() {
  return (
    <Card className="flex h-full min-h-0 flex-col gap-3 rounded-none border-0 border-r bg-transparent p-3 shadow-none">
      <div className="space-y-1 px-1">
        <h2 className="text-sm font-semibold">Sessions</h2>
        <p className="text-xs text-muted-foreground">Chat history</p>
      </div>
      <NewSessionButton />
      <SessionList />
    </Card>
  );
}
