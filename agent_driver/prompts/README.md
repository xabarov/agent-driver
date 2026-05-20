# Agent Prompts

Runtime/system prompt templates are stored in `agent_driver/prompts/templates/`.

- `react_base_policy.txt`: base ReAct system policy.
- `react_chat_tool_policy.txt`: additional chat-mode policy.
- `force_final_answer_user_message.txt`: user nudge appended when runtime must stop tool loop.
- `force_final_answer_tool_message.txt`: protocol message used after tool stage to force final answer.
- `python_tool_system_addendum.txt`: dynamic python tool sandbox/imports block (must include `{imports}`, `{policy_summary}`, `{max_exec_ms}`, `{max_output_chars}`, `{session_idle_seconds}`, and `{scientific_guidance}` placeholders).

Python entrypoints for runtime code are in `agent_driver/prompts/agent.py`.
