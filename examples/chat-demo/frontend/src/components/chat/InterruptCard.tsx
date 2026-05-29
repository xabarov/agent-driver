import { useState } from "react";
import { ShieldAlert } from "lucide-react";

import type { PendingInterrupt } from "../../store/chatStore";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";

interface InterruptCardProps {
  interrupt: PendingInterrupt;
  onAction: (payload: {
    action: string;
    editedToolArgs?: Record<string, unknown>;
    message?: string;
  }) => void;
}

function allows(interrupt: PendingInterrupt, action: string): boolean {
  if (!interrupt.allowedActions.length) {
    return true;
  }
  return interrupt.allowedActions.includes(action);
}

export function InterruptCard({ interrupt, onAction }: InterruptCardProps) {
  const [editJson, setEditJson] = useState(
    JSON.stringify(interrupt.proposedAction ?? {}, null, 2),
  );
  const [clarifyMessage, setClarifyMessage] = useState("");
  const canResume = Boolean(interrupt.interruptId);

  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4">
      <div className="mb-3 flex items-start gap-2">
        <ShieldAlert className="mt-0.5 h-5 w-5 text-amber-400" />
        <div>
          <h3 className="font-semibold">Approval required</h3>
          <p className="text-sm text-muted-foreground">
            {interrupt.title ?? interrupt.reason}
          </p>
          {interrupt.description ? (
            <p className="mt-1 text-sm">{interrupt.description}</p>
          ) : null}
          {!canResume ? (
            <p className="mt-2 text-sm text-destructive">
              Could not load interrupt metadata. Use sqlite store and retry, or check backend logs.
            </p>
          ) : null}
        </div>
      </div>
      <pre className="mb-3 max-h-40 overflow-auto rounded-md border bg-background/80 p-2 text-xs">
        {JSON.stringify(interrupt.proposedAction ?? {}, null, 2)}
      </pre>
      <div className="flex flex-wrap gap-2">
        {allows(interrupt, "approve") ? (
          <Button
            type="button"
            disabled={!canResume}
            onClick={() => onAction({ action: "approve" })}
          >
            Approve
          </Button>
        ) : null}
        {allows(interrupt, "reject") ? (
          <Button
            type="button"
            variant="secondary"
            disabled={!canResume}
            onClick={() => onAction({ action: "reject" })}
          >
            Reject
          </Button>
        ) : null}
        {allows(interrupt, "cancel") ? (
          <Button
            type="button"
            variant="ghost"
            disabled={!canResume}
            onClick={() => onAction({ action: "cancel" })}
          >
            Cancel
          </Button>
        ) : null}
      </div>
      {allows(interrupt, "edit") ? (
        <div className="mt-3 space-y-2">
          <p className="text-xs font-medium text-muted-foreground">Edit tool args (JSON)</p>
          <Textarea value={editJson} onChange={(event) => setEditJson(event.target.value)} rows={4} />
          <Button
            type="button"
            variant="secondary"
            disabled={!canResume}
            onClick={() => {
              try {
                const parsed = JSON.parse(editJson) as Record<string, unknown>;
                onAction({ action: "edit", editedToolArgs: parsed });
              } catch {
                return;
              }
            }}
          >
            Submit edit
          </Button>
        </div>
      ) : null}
      {allows(interrupt, "clarify") ? (
        <div className="mt-3 space-y-2">
          <p className="text-xs font-medium text-muted-foreground">Clarify</p>
          <Textarea
            value={clarifyMessage}
            onChange={(event) => setClarifyMessage(event.target.value)}
            rows={2}
          />
          <Button
            type="button"
            variant="secondary"
            disabled={!canResume}
            onClick={() => onAction({ action: "clarify", message: clarifyMessage.trim() })}
          >
            Send clarification
          </Button>
        </div>
      ) : null}
    </div>
  );
}
