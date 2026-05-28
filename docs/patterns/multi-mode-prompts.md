# Multi-mode prompt assembly

> **When this applies:** your agent has discrete operating modes (e.g.
> `ask` / `plan` / `code`) that should make the model behave differently —
> answer concisely vs. think step-by-step, free exploration vs. strict
> protocol, etc. The same tools and persona apply across modes; only the
> *behavioural contract* changes.
>
> **TL;DR:** prefer **behaviour-block substitution** over **header
> prepending**. Models read prepended-header text as a "modifier on top of
> the real rules" and continue to follow the body. Substitution makes the
> mode-specific rules the *only* spec the model sees for that turn.

## The anti-pattern: prepending a "mode header"

The intuitive first attempt is to render your normal system prompt and
then push a header in front when a special mode is active:

```python
def build_prompt(mode: str) -> str:
    prompt = render_ask_mode_prompt()  # the usual content
    if mode == "plan":
        prompt = PLAN_MODE_HEADER + "\n\n" + prompt
    return prompt
```

`PLAN_MODE_HEADER` typically contains:

```
=== PLAN MODE — READ FIRST ===

The user has explicitly requested PLAN MODE for this turn. This OVERRIDES
the default behaviour described below. Your VERY FIRST action MUST be a
tool call to `todo_write` …
```

This **works in casual benchmarks and fails in production** for two
reasons:

1. **The body of the prompt still teaches "ask" behaviour.** It says
   things like "answer concisely with markdown tables" or "for list-style
   answers return plain comma-separated values". The model reads that as
   the primary contract and treats the prepended header as a side note.
   On ambiguous questions ("проанализируй эту таблицу" — analyse this
   table) it'll happily skip the planning protocol and produce a direct
   prose answer.
2. **The prepended header has no anchor for invariant rules.** Things
   like "respond in the user's language" or "never fabricate numbers"
   live in the ask body. If you tell the model "PLAN MODE OVERRIDES the
   default behaviour", it may decide that invariant rules are also out
   of scope. You then need to restate them in the header — at which
   point the header *is* the new behaviour block, and you've reinvented
   substitution badly.

Symptom in practice: the agent calls `todo_write` once, then writes a
prose answer without invoking any data tool. The plan is fabricated;
the answer is fabricated to match. You can detect this case with
[`agent_driver.runtime.planning_check.planning_executed`](../../agent_driver/runtime/planning_check.py)
— but detection is a fallback for when the prompt didn't hold up.

## The pattern: behaviour-block substitution

Restructure the prompt as a shell with a `{behaviour_block}` placeholder:

```
{persona}                    # who the agent is (shared)

{behaviour_block}            # how the agent operates THIS TURN
                             # ← swap based on mode

---

{tool_catalog}               # what tools exist (shared)

{operating_guidance}         # tool-by-tool tips (shared)
```

Define one behaviour block per mode. The mode-specific block carries:

- The mode-specific contract (e.g. "first action = `todo_write`; after
  each step call `todo_write` with `merge=true`; don't finalise while a
  todo is `in_progress`").
- An echo of the cross-cutting invariants (language, format,
  no-fabrication). Don't rely on the *other* mode's block to carry them
  through.

```python
BEHAVIOUR_BLOCK_ASK = """### GLOBAL RULES:
1. Answer language: respond in {language}.
2. Final answer format: plain Markdown only.
3. ... (etc.)

### HOW TO WORK WITH TOOLS:
- Plan first, then act.
- Iterate.
- ..."""

BEHAVIOUR_BLOCK_PLAN_RU = """### РЕЖИМ ПЛАНИРОВАНИЯ — ВАШ ЕДИНСТВЕННЫЙ КОНТРАКТ:

Пользователь явно включил режим планирования. Это **полностью переопределяет**
поведение по умолчанию.

#### Жёсткие правила:
1. Самый первый ход — `todo_write` с планом из 3–7 пунктов.
2. После КАЖДОГО шага — `todo_write` с merge=true.
3. ... (etc.)

#### Инвариантные правила (применяются И в режиме планирования):
- Язык: {language}.
- Формат: plain Markdown.
- Никаких выдуманных чисел.
- ..."""

def build_system_prompt(mode: str, language: str, ...) -> str:
    block = BEHAVIOUR_BLOCK_PLAN_RU if mode == "plan" else BEHAVIOUR_BLOCK_ASK
    return SHELL.format(
        persona=persona,
        behaviour_block=block.format(language=language),
        tool_catalog=tool_catalog,
        operating_guidance=operating_guidance,
    )
```

## What stays shared

Behaviour-block substitution is *only* for behavioural rules. Don't
duplicate:

- **Tool catalog** — the model needs the same tool list either way.
  Trying to hide tools in one mode usually backfires: if you hide
  `sandbox_execute_pandas` in plan mode, the model builds a plan it can't
  execute and stalls. Let prompt rules constrain *when* a tool is used,
  not whether it exists.
- **Operating guidance** (tips like "for filtering use pandas, not
  excel_find") — same logic.
- **Persona** — the agent's role doesn't change just because the user
  toggled a UI chip.
- **Workbook / context overview** — pure data, no rules.

## Measured effect

This pattern was introduced in `excel_ai` after the prepend approach
failed on roughly half of plan-mode questions on `qwen3-235b`. After
substitution:

- **Single-clause questions** ("how many rows; top 3 values in column X")
  → the model now reliably runs the plan and produces grounded answers.
- **Heavy multi-clause questions** (3+ analytical asks in one prompt) →
  the model still fabricates on this corpus. Prompt-only mitigation has
  a ceiling; for these cases combine with
  [`planning_executed`](../../agent_driver/runtime/planning_check.py)
  detection + an auto-retry directive (caller-side), or move to a more
  compliant model.

The takeaway is not "substitution always works" but **"the prepend
approach has a structural ceiling that substitution doesn't"**. With
substitution, when the model fails, it's the model's limit. With
prepending, the prompt design is contributing to the failure.

## Reference implementation

See `excel_ai`'s prompt assembly:

- Shell + placeholder:
  `excel_ai/backend/excel_agent/prompts/components/base_prompt.py`
  (`EXCEL_AGENT_SYSTEM_PROMPT_BASE`,
  `BEHAVIOUR_BLOCK_ASK`,
  `BEHAVIOUR_BLOCK_PLAN_RU`,
  `BEHAVIOUR_BLOCK_PLAN_EN`)
- Mode selection:
  `excel_ai/backend/excel_agent/prompts/system_prompt.py`
  (`build_system_prompt(..., mode=...)`,
  `_select_behaviour_block`)
- Call-site:
  `excel_ai/backend/excel_agent/core/orchestrator/agent_creation.py`
  (passes `mode` through; no longer prepends a header)

The Excel project used to call a `_augment_prompt_for_plan_mode(prompt)`
helper that did the bad pattern; that helper was deleted in the
substitution pass.
