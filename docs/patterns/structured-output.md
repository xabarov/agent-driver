# Structured Output

Use structured output when a downstream component needs a validated object
instead of prose.

## Preferred Options

1. Provider-level `response_format`.
   Use this when the provider supports JSON object or JSON schema enforcement.
   It is best for final structured responses where no tool execution is needed.

2. Forced schema-like tool call.
   Define a tool whose args schema is the desired object, then call the model
   with `tool_choice={"type": "tool", "name": "..."}` and `max_tool_calls=1`.
   This is useful when the provider's structured-output support is weak or when
   the object should travel through the existing tool trace path.

3. Optional Instructor adapter.
   Use the optional structured extraction layer when we want Pydantic
   validation and retry/reask behavior at a boundary, without making Instructor
   part of the core runtime.

## Current Runtime Contract

`AgentRunInput.response_format` accepts provider-level structured-output hints
that flow into `LlmRequest.response_format`.

Common shapes:

```python
{"type": "json_object"}
```

```python
{
    "type": "json_schema",
    "json_schema": {
        "name": "report_plan",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "sections": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "sections"],
            "additionalProperties": False,
        },
        "strict": True,
    },
}
```

Provider support varies. OpenAI-compatible providers can enforce the shape at
decode time when the backend supports it; other providers may need prompt-level
guidance plus post-call validation.

## Design Rule

Do not add a complex workflow just to parse one object. Start with
`response_format`, a forced tool call, or the optional structured adapter. Move
to heavier orchestration only when traces show repeated failures that simple
validation and retry cannot fix.
