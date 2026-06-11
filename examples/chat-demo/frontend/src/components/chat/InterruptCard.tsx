import { useId, useState } from "react";
import { FileText, Hash, ShieldAlert } from "lucide-react";

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

interface PlanApprovalView {
  plan_id?: string;
  content?: string;
  content_hash?: string;
  path?: string | null;
}

interface ClarificationChoice {
  id: string;
  label: string;
  description?: string;
}

interface ClarificationQuestion {
  id: string;
  header: string;
  question: string;
  preview?: string;
  choices: ClarificationChoice[];
}

function getPlanApproval(interrupt: PendingInterrupt): PlanApprovalView | null {
  const proposed = interrupt.proposedAction;
  const payload = proposed?.plan_approval;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return null;
  }
  return payload as PlanApprovalView;
}

function getClarificationQuestions(
  interrupt: PendingInterrupt,
): ClarificationQuestion[] {
  const raw = interrupt.proposedAction?.questions;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.filter((item): item is ClarificationQuestion => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      return false;
    }
    const candidate = item as Partial<ClarificationQuestion>;
    return (
      typeof candidate.id === "string" &&
      typeof candidate.header === "string" &&
      typeof candidate.question === "string" &&
      Array.isArray(candidate.choices)
    );
  });
}

export function InterruptCard({ interrupt, onAction }: InterruptCardProps) {
  const planApproval = getPlanApproval(interrupt);
  const isClarification = interrupt.reason === "clarification_required";
  const clarificationQuestions = isClarification
    ? getClarificationQuestions(interrupt)
    : [];
  const heading = planApproval
    ? "Plan approval required"
    : isClarification
      ? "Clarification required"
      : "Approval required";
  const showRawAction = !planApproval && !isClarification;
  const [editJson, setEditJson] = useState(
    JSON.stringify(interrupt.proposedAction ?? {}, null, 2),
  );
  const [planEdit, setPlanEdit] = useState(planApproval?.content ?? "");
  const [clarifyMessage, setClarifyMessage] = useState("");
  const canResume = Boolean(interrupt.interruptId);
  const clarifyId = useId();

  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4">
      <div className="mb-3 flex items-start gap-2">
        <ShieldAlert className="mt-0.5 h-5 w-5 text-amber-400" />
        <div>
          <h3 className="font-semibold">
            {heading}
          </h3>
          <p className="text-sm text-muted-foreground">
            {interrupt.title ?? interrupt.reason}
          </p>
          {interrupt.description ? (
            <p className="mt-1 text-sm">{interrupt.description}</p>
          ) : null}
          {!canResume ? (
            <p className="mt-2 text-sm text-destructive">
              Could not load interrupt metadata. Use sqlite store and retry, or check
              backend logs.
            </p>
          ) : null}
        </div>
      </div>
      {planApproval ? (
        <div className="mb-3 space-y-3">
          <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
            {planApproval.path ? (
              <span className="inline-flex items-center gap-1 rounded border bg-background/70 px-2 py-1">
                <FileText className="h-3.5 w-3.5" />
                {planApproval.path}
              </span>
            ) : null}
            {planApproval.content_hash ? (
              <span className="inline-flex max-w-full items-center gap-1 rounded border bg-background/70 px-2 py-1">
                <Hash className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">{planApproval.content_hash}</span>
              </span>
            ) : null}
          </div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border bg-background/80 p-3 text-sm leading-6">
            {planApproval.content}
          </pre>
        </div>
      ) : showRawAction ? (
        <pre className="mb-3 max-h-40 overflow-auto rounded-md border bg-background/80 p-2 text-xs">
          {JSON.stringify(interrupt.proposedAction ?? {}, null, 2)}
        </pre>
      ) : null}
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
          {planApproval ? (
            <>
              <p className="text-xs font-medium text-muted-foreground">Edit plan</p>
              <Textarea
                value={planEdit}
                onChange={(event) => setPlanEdit(event.target.value)}
                rows={6}
              />
              <Button
                type="button"
                variant="secondary"
                disabled={!canResume || !planEdit.trim()}
                onClick={() =>
                  onAction({
                    action: "edit",
                    editedToolArgs: {
                      ...((interrupt.proposedAction?.args as
                        | Record<string, unknown>
                        | undefined) ?? {}),
                      content: planEdit.trim(),
                    },
                  })
                }
              >
                Submit plan edit
              </Button>
            </>
          ) : (
            <>
              <p className="text-xs font-medium text-muted-foreground">
                Edit tool args (JSON)
              </p>
              <Textarea
                value={editJson}
                onChange={(event) => setEditJson(event.target.value)}
                rows={4}
              />
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
            </>
          )}
        </div>
      ) : null}
      {allows(interrupt, "clarify") ? (
        <div className="mt-3 space-y-2">
          {clarificationQuestions.length ? (
            <div className="space-y-3 rounded-md border bg-background/60 p-3">
              {clarificationQuestions.map((question) => (
                <div key={question.id} className="space-y-2">
                  <div>
                    <p className="text-xs font-semibold uppercase text-muted-foreground">
                      {question.header}
                    </p>
                    <p className="text-sm">{question.question}</p>
                    {question.preview ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {question.preview}
                      </p>
                    ) : null}
                  </div>
                  {question.choices.length ? (
                    <div className="flex flex-wrap gap-2">
                      {question.choices.map((choice) => (
                        <Button
                          key={choice.id}
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => {
                            const prefix =
                              clarificationQuestions.length > 1
                                ? `${question.header}: `
                                : "";
                            setClarifyMessage(`${prefix}${choice.label}`);
                          }}
                        >
                          {choice.label}
                        </Button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
          <label
            className="block text-xs font-medium text-muted-foreground"
            htmlFor={clarifyId}
          >
            Clarify
          </label>
          <Textarea
            id={clarifyId}
            value={clarifyMessage}
            onChange={(event) => setClarifyMessage(event.target.value)}
            rows={2}
          />
          <Button
            type="button"
            variant="secondary"
            disabled={!canResume || !clarifyMessage.trim()}
            onClick={() =>
              onAction({ action: "clarify", message: clarifyMessage.trim() })
            }
          >
            Send clarification
          </Button>
        </div>
      ) : null}
    </div>
  );
}
