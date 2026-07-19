# Goal-directed continuation: петля держится цели, а не только бюджетов

Дата создания: 2026-07-19 (из horizon-scan 020, кандидат №2). Статус: **proposed**.

Мотивация из живых данных: «модель хочет искать дальше, а её обрывают» (скрытая «1» бюджета,
2f8e3c6) и остаточный флейк «энумерация решений покрывает не все встречи» (наблюдение
2026-07-19: forced-final срабатывает у бюджета, пока модель ещё добирает отчёты). Бюджеты
решают «когда остановить»; цель должна решать «закончена ли работа».

## Reference-first

- **openclaude `src/services/goal/`** — controller/evaluator/persistence: цель формулируется,
  прогресс оценивается, stop-hooks с goal-тестами удерживают петлю до достижения
  (`goalContinuation`, `src/query/stopHooks.ts`).
- **hermes verification_stop** — verify-on-stop: попытка закончить без свежих evidence
  превращается в bounded follow-up.
- Родня в нашем движке: progress-only-continue (epic 015), research_session_contract
  (FINAL_READINESS), deliverable-repair — фрагменты той же идеи, разрозненные.

## Эскиз фаз

A. Контракт цели: GoalSpec (что считается «сделано», проверяемые критерии) в AgentRunInput /
   metadata; evaluator как lifecycle-hook перед finalize (переиспользовать
   research_session_contract-механику как частный случай).
B. Continuation: незакрытая цель + оставшийся бюджет → bounded re-prompt с конкретикой «чего не
   хватает» (как budget-aware hint из наблюдения «N calls left»).
C. Связка с 021: цель может ссылаться на memory-факты (персистентные критерии пользователя).
D. Бенч: энумерационные кейсы Аргуса (decisions_log 3/3 стабильно) как приёмка.
