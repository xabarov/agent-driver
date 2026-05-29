# HITL — mapping host HTTP payloads to ResumeCommand

This note describes the contract between host applications (FastAPI, Flask,
Django, etc.) and the agent runtime when a human operator approves,
rejects, edits, or clarifies an interrupted run.

## Why a dedicated helper

A typical web application exposes a single HITL endpoint such as

```
POST /api/conversations/{id}/approve
{"resume": "yes", "interrupt_id": "intr_42", ...}
```

The body shape varies wildly across products: some use string aliases
(`approve`/`reject`), some use legacy integer ``choice`` codes (1/2/3),
some pass an opaque ``resume`` / ``answer`` / ``value`` field that the
runtime is expected to interpret. Without a shared normalizer every host
re-implements the same mapping into the typed :class:`ResumeCommand`, and
those implementations drift in subtle ways (different APPROVE aliases,
inconsistent CLARIFY handling, etc.).

:func:`agent_driver.sdk.resume_command_from_payload` is that shared
normalizer.

## Recognized payload shapes

The helper inspects three families of fields in priority order:

1. **Explicit action.** ``action`` field with a :class:`ResumeAction`
   value or string alias (``approve``/``yes``/``accept``,
   ``reject``/``no``/``deny``, ``edit``/``modify``,
   ``cancel``/``abort``, ``clarify``, ``patch_state``/``state_patch``).
2. **Legacy integer choice.** ``choice`` field with 1 (APPROVE), 2 (EDIT),
   or 3 (CANCEL). Choice 2 must be accompanied by ``edited_tool_args`` or
   ``state_patch`` to satisfy :class:`ResumeCommand` invariants; the
   helper does not silently fabricate an empty edit payload.
3. **Opaque resume value.** ``resume`` / ``answer`` / ``value`` field
   carrying any of:

   - boolean ``True`` / ``False`` → APPROVE / REJECT;
   - integer ``1`` / ``0`` → APPROVE / REJECT (legacy ``2`` / ``3`` are
     forwarded to the choice table);
   - string aliases through the standard table;
   - dict payload → EDIT, with ``edited_tool_args`` inferred from the dict
     when the host has not supplied one explicitly;
   - non-empty unrecognized string → CLARIFY, with the string used as
     ``message`` when the host has not supplied one explicitly;
   - non-empty list → CLARIFY, with the JSON-encoded list as ``message``.

   Hosts can override the default mapping by passing
   ``value_to_action=...`` (a callable that returns a :class:`ResumeAction`
   or ``None``).

If none of the three families produces an action the helper raises
``ValueError`` so the host can return HTTP 400. ``TypeError`` is raised
for malformed field types (non-string ``message``, non-Mapping
``edited_tool_args`` or ``state_patch``, etc.).

## interrupt_id resolution

``interrupt_id`` is resolved in this order: explicit field in the body,
``default_interrupt_id`` kwarg, otherwise ``ValueError``. The kwarg is
useful when the host derives the interrupt id from a URL path parameter
or from the latest pending interrupt for the current conversation.

## Reference adapter for LangGraph-style hosts

A ZION-style ``POST /api/conversations/{id}/approve`` endpoint that
forwards to ``Agent.resume(...)`` would look like:

```python
from agent_driver.sdk import (
    Agent,
    resume_command_from_payload,
)


@router.post("/conversations/{conversation_id}/approve")
async def resume_endpoint(
    conversation_id: str,
    body: dict,
    agent: Agent,
) -> dict:
    command = resume_command_from_payload(
        body,
        default_interrupt_id=conversation_id,
    )
    output = await agent.resume(
        run_id=conversation_id,
        interrupt_id=command.interrupt_id,
        action=command.action,
        edited_tool_args=command.edited_tool_args,
        message=command.message,
    )
    return {"ok": True, "status": output.status.value}
```

The same helper handles all three input families above; the endpoint code
stays the same as new aliases are added to the table.

## interrupt_to_stream_event

The complementary helper
:func:`agent_driver.sdk.interrupt_to_stream_event` turns an
:class:`InterruptRequest` into a transport-neutral dictionary that hosts
can wrap into their own SSE envelope:

```python
from agent_driver.sdk import interrupt_to_stream_event


def on_interrupt(interrupt):
    projection = interrupt_to_stream_event(interrupt, event_type="plan.proposed")
    yield sse_event({"type": "plan.proposed", "payload": projection})
```

The returned dict contains the full interrupt identity (run/attempt/
checkpoint), the reason, title, description, optional risk level, allowed
resume actions, editable field names, expiry, raw ``proposed_action``,
and a deterministic ``approval_payload`` card built via
:meth:`ApprovalPayload.from_interrupt`. Hosts can freely add their own
fields around or inside the envelope without modifying the runtime.

## Domain isolation

What the runtime promises:

- a stable contract for normalizing common HTTP body shapes into
  :class:`ResumeCommand`;
- a stable projection of :class:`InterruptRequest` into a dict that any
  transport (SSE / WebSocket / queue) can carry.

What the runtime does **not** promise:

- specific HTTP route names (``/approve`` vs ``/resume`` vs ...);
- specific SSE event types (``plan.proposed`` vs ``interrupt`` vs ...);
- application authentication / authorization for the resume actor;
- domain-specific decoding of opaque ``resume`` values beyond the default
  rule table (hosts that have richer semantics — e.g. multi-choice plan
  approvals — pass a custom ``value_to_action``).

That separation lets the same runtime power a ZION-style ``plan.proposed``
endpoint, a CLI ``agent-driver chat`` approval slash command, and any
other product surface without forking the contract.
