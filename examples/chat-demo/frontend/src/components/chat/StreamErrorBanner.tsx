import { AlertCircle, X } from "lucide-react";

import { useChatStore } from "../../store/chatStore";
import { Button } from "../ui/button";

export function StreamErrorBanner() {
  const lastError = useChatStore((state) => state.lastError);
  const setLastError = useChatStore((state) => state.setLastError);

  if (!lastError) {
    return null;
  }

  return (
    <div className="flex items-start gap-2 rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <p className="flex-1">{lastError}</p>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="h-7 w-7 shrink-0 text-destructive hover:text-destructive"
        aria-label="Dismiss error"
        onClick={() => setLastError(undefined)}
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}
