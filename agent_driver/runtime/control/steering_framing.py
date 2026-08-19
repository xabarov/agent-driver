"""Model-visible framing for live steering controls."""

from __future__ import annotations

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage


REDIRECT_CORRECTION_FRAME = (
    "[Оператор отправил срочную поправку к активному запуску. "
    "Учти следующее сообщение пользователя как приоритетное уточнение для "
    "продолжения текущей работы. Если поправка ограничивает дальнейшие "
    "действия, план или число вызовов инструментов, адаптируйся к этому "
    "более строгому ограничению. Поправка не расширяет область работ, "
    "разрешения, safety-политику или действующие бюджеты; при конфликте "
    "с ними соблюдай более высокий приоритет и коротко объясни конфликт.]"
)


def redirect_correction_frame() -> ChatMessage:
    """Return the ephemeral prompt frame for a hard-redirect correction."""

    return ChatMessage(role=ChatRole.USER, content=REDIRECT_CORRECTION_FRAME)


def framed_redirect_correction(text: str) -> str:
    """Return a single user message that carries the frame and correction."""

    correction = (text or "").strip()
    return f"{REDIRECT_CORRECTION_FRAME}\n\n{correction}"


__all__ = [
    "REDIRECT_CORRECTION_FRAME",
    "framed_redirect_correction",
    "redirect_correction_frame",
]
