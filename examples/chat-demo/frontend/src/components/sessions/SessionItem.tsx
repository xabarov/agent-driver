import { MoreHorizontal, Trash2 } from "lucide-react";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";

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
          "group flex items-center gap-1 rounded-md border px-2 py-1.5 text-sm transition-colors",
          isActive
            ? "border-primary/60 border-l-2 border-l-primary bg-accent/50 pl-[calc(0.5rem-1px)]"
            : "border-border border-l-2 border-l-transparent hover:bg-muted/40",
        )}
      >
        <Link to={`/sessions/${session.session_id}`} className="min-w-0 flex-1 space-y-0.5">
          <p className="truncate font-medium leading-snug">{session.title}</p>
          <p className="text-xs text-muted-foreground">{session.runs_count} runs</p>
        </Link>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className={cn(
                "relative z-10 h-7 w-7 shrink-0 text-muted-foreground",
                "opacity-80 transition-opacity hover:bg-muted/60 hover:text-foreground",
                "group-hover:opacity-100 group-focus-within:opacity-100",
                "focus-visible:opacity-100 data-[state=open]:bg-muted/60 data-[state=open]:opacity-100",
              )}
              aria-label={`Session options for ${session.title}`}
              onClick={(event) => event.stopPropagation()}
            >
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" onClick={(event) => event.stopPropagation()}>
            <DropdownMenuItem
              className="text-destructive focus:text-destructive"
              onSelect={() => setConfirmOpen(true)}
            >
              <Trash2 className="mr-2 h-4 w-4" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
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
