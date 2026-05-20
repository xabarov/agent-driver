import { useState } from "react";

import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";

interface ComposerInputProps {
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

export function ComposerInput({ streaming, onSend, onStop }: ComposerInputProps) {
  const [value, setValue] = useState("");

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || streaming) {
      return;
    }
    onSend(trimmed);
    setValue("");
  };

  return (
    <div className="space-y-2">
      <Textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Type your message..."
        disabled={streaming}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          Cmd/Ctrl + Enter to send
        </p>
        {streaming ? (
          <Button type="button" variant="destructive" onClick={onStop}>
            Stop
          </Button>
        ) : (
          <Button type="button" onClick={submit}>
            Send
          </Button>
        )}
      </div>
    </div>
  );
}
