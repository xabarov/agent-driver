import { MessageSquarePlus } from "lucide-react";
import { useCallback } from "react";
import { useNavigate } from "react-router-dom";

import { useChatStore } from "../../store/chatStore";
import { Button } from "../ui/button";

export function NewSessionButton() {
  const navigate = useNavigate();
  const reset = useChatStore((state) => state.reset);

  const handleClick = useCallback(() => {
    reset();
    navigate("/sessions/new");
  }, [navigate, reset]);

  return (
    <Button type="button" className="w-full justify-start gap-2" onClick={handleClick}>
      <MessageSquarePlus className="h-4 w-4" />
      New session
    </Button>
  );
}
