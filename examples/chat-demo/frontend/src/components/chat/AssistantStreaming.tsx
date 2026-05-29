import { Loader2 } from "lucide-react";

export function AssistantStreaming() {
  return (
    <span
      className="mt-2 inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground"
      aria-live="polite"
    >
      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
      Writing
    </span>
  );
}
