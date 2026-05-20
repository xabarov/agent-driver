import { Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useDeleteSession } from "../../lib/sessions";
import { cn } from "../../lib/cn";
import type { SessionSummaryView } from "../../types/api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../ui/alert-dialog";
import { Button } from "../ui/button";

interface SessionItemProps {
  session: SessionSummaryView;
}

export function SessionItem({ session }: SessionItemProps) {
  const params = useParams<{ id: string }>();
  const navigate = useNavigate();
  const deleteSession = useDeleteSession();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const isActive = params.id === session.session_id;

  const handleDelete = () => {
    void deleteSession.mutateAsync(session.session_id).then(() => {
      setConfirmOpen(false);
      if (isActive) {
        navigate("/sessions/new", { replace: true });
      }
    });
  };

  return (
    <>
      <div
        className={cn(
          "grid w-full max-w-full min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-1 rounded-md border py-1.5 pr-1 pl-2 text-sm transition-colors",
          isActive
            ? "border-primary/60 border-l-2 border-l-primary bg-accent/50 pl-[calc(0.5rem-1px)]"
            : "border-border border-l-2 border-l-transparent hover:bg-muted/40",
        )}
      >
        <Link
          to={`/sessions/${session.session_id}`}
          className="min-w-0 overflow-hidden py-0.5 pr-1"
        >
          <p className="truncate font-medium leading-snug">{session.title}</p>
          <p className="truncate text-xs text-muted-foreground">{session.runs_count} runs</p>
        </Link>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-8 w-8 shrink-0 text-destructive hover:bg-destructive/15 hover:text-destructive"
          aria-label={`Delete session ${session.title}`}
          title="Delete session"
          disabled={deleteSession.isPending}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setConfirmOpen(true);
          }}
        >
          <Trash2 className="h-4 w-4" strokeWidth={2} />
        </Button>
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete session?</AlertDialogTitle>
            <AlertDialogDescription>
              Delete session &ldquo;{session.title}&rdquo;? This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteSession.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:opacity-90"
              disabled={deleteSession.isPending}
              onClick={(event) => {
                event.preventDefault();
                handleDelete();
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
