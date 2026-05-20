import { Trash2 } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useDeleteSession } from "../../lib/sessions";
import { cn } from "../../lib/cn";
import type { SessionSummaryView } from "../../types/api";
import { Button } from "../ui/button";

interface SessionItemProps {
  session: SessionSummaryView;
}

export function SessionItem({ session }: SessionItemProps) {
  const params = useParams<{ id: string }>();
  const navigate = useNavigate();
  const deleteSession = useDeleteSession();
  const isActive = params.id === session.session_id;

  return (
    <div
      className={cn(
        "group flex items-start gap-2 rounded-md border p-2 text-sm",
        isActive ? "border-primary bg-secondary" : "border-border",
      )}
    >
      <Link to={`/sessions/${session.session_id}`} className="min-w-0 flex-1 space-y-1">
        <p className="truncate font-medium">{session.title}</p>
        <p className="text-xs text-muted-foreground">{session.runs_count} runs</p>
      </Link>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="h-7 w-7 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
        aria-label={`Delete ${session.title}`}
        onClick={() => {
          void deleteSession.mutateAsync(session.session_id).then(() => {
            if (isActive) {
              navigate("/sessions/new", { replace: true });
            }
          });
        }}
        disabled={deleteSession.isPending}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
