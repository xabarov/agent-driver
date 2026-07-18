# Loop guards plane: wall-clock, tool-failure и прочие независимые предохранители петли

Дата создания: 2026-07-18. Статус: **proposed**.

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
