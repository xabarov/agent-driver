"""E8: defensive message sanitization before provider calls.

Some content reaches the runtime with lone UTF-16 surrogates (e.g. text decoded
with ``errors="surrogateescape"``) or embedded NUL bytes. Such strings are valid
Python ``str`` objects but raise ``UnicodeEncodeError`` on ``.encode("utf-8")``,
so the HTTP/JSON request to the provider fails outright. This module strips
exactly those two hazards and nothing else — legitimate Unicode (accents, emoji,
CJK) is preserved. Targeted and conservative, mirroring hermes' message
sanitization. Pure and deterministic.
"""

from __future__ import annotations

from agent_driver.contracts.messages import ChatMessage

# Lone UTF-16 surrogates (U+D800–U+DFFF) cannot encode to UTF-8; NUL breaks
# many JSON/HTTP stacks. Drop both; keep everything else.
_HAZARDS = {"\x00"}


def strip_surrogates(text: str) -> str:
    """Remove lone UTF-16 surrogates and NUL bytes from ``text``.

    Returns ``text`` unchanged when it is already clean (cheap fast path).
    """
    if text.isascii():
        # ASCII can still contain NUL; surrogates are impossible.
        return text.replace("\x00", "") if "\x00" in text else text
    cleaned_chars = [
        ch for ch in text if not ("\ud800" <= ch <= "\udfff") and ch not in _HAZARDS
    ]
    cleaned = "".join(cleaned_chars)
    return cleaned if len(cleaned) != len(text) else text


def sanitize_request_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Return messages with surrogate/NUL-safe content (copy only when needed).

    Messages whose content is already clean are returned unchanged (identity),
    so this is cheap on the common path and only allocates for the rare dirty
    message.
    """
    sanitized: list[ChatMessage] = []
    for message in messages:
        clean = strip_surrogates(message.content)
        if clean is message.content:
            sanitized.append(message)
        else:
            sanitized.append(message.model_copy(update={"content": clean}))
    return sanitized


__all__ = ["sanitize_request_messages", "strip_surrogates"]
