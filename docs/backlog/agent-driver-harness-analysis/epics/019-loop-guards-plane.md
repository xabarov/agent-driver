# Loop guards plane: wall-clock, tool-failure и прочие независимые предохранители петли

Дата создания: 2026-07-18. Статус: **done** (2026-07-19).

> Реализация: фаза A — wall-clock guards в раннере: три независимых источника таймаута шага
> (per-run deadline / config `default_hard_max_seconds`=1800 / `default_idle_timeout_seconds`=300
> как кап на ОДИН степ — режет подвисший tool/провайдер даже при свободном бюджете), самый
> жёсткий побеждает, вид гарда в terminal-payload (`wall_clock_guard`). Фаза B —
> tool-failure guard: серия одинаковых ФЕЙЛОВ (tool+код ошибки), warn на 2-м
> (`tool_failure_streak_warning`), форс-финал на 3-м (`tool_failure_streak` в
> `_force_final_reason`); успех/смена сигнатуры сбрасывают серию. Фаза C — конфигурируемые
> детские бюджеты (`SubagentSettings.default_child_max_steps/max_tool_calls`, штамп в
> task.metadata, явные значения планировщика приоритетны) + структурированный маркер
> `child_budget` (budget_exhausted/terminal_reason/лимиты) в результате ребёнка для родителя.
> Фаза D — refund housekeeping-вызовов (planning_state_update/planning_progress/todo_write не
> сжигают tool-бюджет: `refunded_tool_calls` вычитается в force-final и журнальном терминале);
> preserved-answer-at-budget уже покрыт связкой budget-grace → forced-final ladder (016).
> Полная регрессия runtime/context/llm/subagents/contracts зелёная.

Источник: инцидент «скрытая 1» (бюджет-фолбэк, починен 5be13a2) показал, что бюджетная
плоскость была неконсистентной; сверка с референсами — у них предохранители петли шире и
независимее друг от друга.

## Reference-first

- **openclaude `src/utils/QueryGuard.ts`** — wall-clock guards: idle 5 мин / hard-max 30 мин
  (env-override), отдельные abort-reasons; НЕЗАВИСИМЫ от шаговых бюджетов.
- **openclaude `src/query/toolFailureLoopGuard.ts`** — 3 повтора одной сигнатуры/категории/пути
  фейла инструмента → tripped-стоп, с предупреждением ДО остановки (#1927).
- **openclaude #1815** — per-agent step limits для сабагентов: при исчерпании возвращается
  структурированный `{reason: 'agent_step_limit', stepsUsed, maxSteps}` + запрашивается summary.
- **hermes `agent/iteration_budget.py`** — refund() для «дешёвых» итераций (execute_code),
  независимые бюджеты сабагентов; `preserved_verification_fallback` — если готовый ответ уже
  придержан гейтом, при исчерпании бюджета отдать ЕГО, а не делать новый fallible-запрос.

## Чего не хватает agent-driver

Есть: шаговые/tool-бюджеты с бэкстопами (5be13a2), grace synthesis, repeated-tool-call
detection, deadline_seconds (per-run). Не хватает:
1. **Idle wall-clock guard** уровня раннера (частично есть stream idle — нужен run-level).
2. **Tool-failure guard**: повторные ФЕЙЛЫ инструмента (не повторные вызовы) по
   сигнатуре/категории → warn → стоп; наш loop_detected ловит только идентичные вызовы.
3. **Per-subagent бюджеты** со структурированным исчерпанием + summary (сабагентная плоскость).
4. **Refund-семантика** для итераций, не тратящих прогресс.

## Фазы

A. Run-level idle/hard-max wall-clock (config, независимые abort-reasons, события).
B. Tool-failure guard c warn-before-stop.
C. Субагентные бюджеты + структурированный limit-результат.
D. Refund + preserved-answer-at-budget (сверить с нашим budget_grace_enabled).
