"""OpenAI-compatible request/response translation for the HTTP server."""

from __future__ import annotations

from agent_driver.server.openai.schema import ChatCompletionRequest, ChatMessageIn

__all__ = ["ChatCompletionRequest", "ChatMessageIn"]
