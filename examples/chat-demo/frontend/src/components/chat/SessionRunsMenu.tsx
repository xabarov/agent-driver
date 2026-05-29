import { Link } from "react-router-dom";
import { History } from "lucide-react";

import { Button } from "../ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";

interface SessionRunsMenuProps {
  sessionId: string;
  runIds: string[];
}

export function SessionRunsMenu({ sessionId, runIds }: SessionRunsMenuProps) {
  if (runIds.length === 0) {
    return null;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" size="sm" variant="ghost" className="gap-1.5 text-muted-foreground">
          <History className="h-4 w-4" />
          Runs ({runIds.length})
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>Replay run</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {runIds.map((runId) => (
          <DropdownMenuItem key={runId} asChild>
            <Link to={`/sessions/${sessionId}/replay/${runId}`} className="font-mono text-xs">
              {runId}
            </Link>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
