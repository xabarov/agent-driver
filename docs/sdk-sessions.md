# SDK Sessions

Use `Session` when an application wants a durable thread id across turns.

```python
session = agent.session("customer_42")
first = await session.send("Start a troubleshooting checklist")
second = await session.send("Add the network checks")

print(session.runs())
print([turn.metadata.get("run_id") for turn in session.history()])
```

Session methods:

- `send(text, ...)` awaits one turn.
- `stream(text, ...)` returns `RunStream`.
- `start(text, ...)` returns `RunHandle` for a background turn.
- `resume(...)` resumes an interrupted run in the same thread.
- `history()` returns persisted `SessionTurn` rows.
- `runs()` returns known run ids in turn order.
- `fork(...)` delegates to the SDK subagent helper.

The session id maps to `AgentRunInput.thread_id`; the runtime still owns
checkpoints, event logs, memory projection and context diagnostics.
