# Multi-Mode Prompts

Use this pattern when the same agent can operate in different modes: normal
chat, live planning, approval planning, deliverable writing, code execution, or
coordinator/subagent work.

## Current Rule

Prefer explicit behavior blocks and compact runtime reminders over piling more
headers onto one large system prompt.

In `agent-driver` this currently shows up as:

- a shared ReAct base policy;
- chat-specific tool policy;
- tool docs generated from the active `ToolRegistry`;
- runtime reminders such as `planning_mode_active`,
  `planning_mode_sparse`, `planning_mode_exit`, and
  `deliverable_request_active`;
- run metadata such as `planning_hint` and `deliverable_request`.

## Good Shape

Keep stable parts shared:

- persona and safety invariants;
- tool catalog;
- workspace/context facts;
- output budgets and tool-risk policy.

Swap or append only the behavior that is actually mode-specific:

- "keep this as a visible todo checklist";
- "stay read-only and prepare an approval plan";
- "the user asked for the final draft now";
- "continue execution after an approved plan";
- "coordinator must synthesize worker results instead of inventing them".

## Anti-Pattern

Avoid a huge prompt where each mode adds another loud "OVERRIDE EVERYTHING"
header. Models often treat the old body as the real contract and the new header
as a weak hint. This caused exactly the kind of failures we are trying to avoid:
repeated planning, clarification loops, and final-answer avoidance.

## Runtime Tie-In

Prompt mode should not be the only guard. If the behavior matters to product
correctness, add a small runtime check:

- deny modal plan tools for explicit deliverable turns;
- force `tool_choice="none"` after enough data has been gathered;
- keep `ask_user_question` bounded and structured;
- block side-effect tools until an approval plan exists when force planning is
  enabled.

This keeps the design in the Python Zen lane: readable prompt contracts first,
small guards where traces show the model needs help.
