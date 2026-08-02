# Status ledger — U1–U7 / R0–R6 (авторитетный источник статуса)

Этот файл — **единственный** авторитетный источник статуса по PentestLens embedding Goal.
`upstream-requirements.md` — **неизменяемый approved input** (SHA
`d4ed6c371eda50e6c0b7fa07df55974cfac7411e32a95708a3f203cbcd526316`); статус/evidence НЕ пишутся в него
(это было нарушением, откачено — см. R0). Статус ведётся здесь + в `epics/` + в handoff.

Правило: пункт `DONE` только после исполнения исходного текста требования **и** acceptance;
ослабления (напр. SQLite вместо Postgres) не применяются без согласования с владельцем PentestLens.

## Исходный Goal (U1–U7) — фактический статус на 2026-08-02

| U | Тема | Эпик | Статус | Примечание |
|---|---|---|---|---|
| U1 | Supported embedding facade | 049 | **DONE** | но `agent_driver.embedding` (post-cut `7bf1c6d`) НЕ в wheel `0.2.0` → войдёт в 0.3.0 (R5) |
| U2 | ToolGate identity + provenance | 050 | **IN PROGRESS** | interrupt/envelope есть; terminal+trace+adversarial-matrix — R1/эпик 057 |
| U3 | Atomic approval + resume | 051 | **IN PROGRESS** | SQLite/in-mem CAS есть; **Postgres durable — R2/эпик 058** |
| U4 | Durable Stop + cancellation | 052 | **IN PROGRESS** | код есть, но deadline (post-cut `d43720d`) НЕ в wheel `0.2.0`; полнота матрицы — R4/эпик 060 |
| U5 | Plan integrity extension point | 053 | **IN PROGRESS** | hash+enforcement+PlanArtifact есть; **trace projection — R3/эпик 059** |
| U6 | Gateway truthfulness | 054 | **DONE** | process-local/non-durable + fail-fast (Option 2) — принято аудитом как допустимое |
| U7 | Version/release/handoff | 055 | **SUPERSEDED** | `0.2.0` не переиспользуется; корректный релиз — 0.3.0/R5/эпик 061 |

**Зонтичный эпик 048:** `IN PROGRESS (remediation phase)`. `complete` — только после acceptance R0–R6.

## Remediation (R0–R6) — статус

| R | Эпик | Статус | Что сделано |
|---|---|---|---|
| R0 | 056a | **DONE** | контракт восстановлен до SHA `d4ed6c…`; статусы вынесены сюда |
| R1 | 057 | pending | — |
| R2 | 058 | **DONE** | 4 PG-стора + generic schema + facade + обязательный CI-job + **20 real-PG тестов** (validated на postgres:15): 17 store-unit (two-client race, replay, conflict, монотонный abort-CAS, restart, parity) + 3 resume-integration end-to-end (duplicate→conflict, два конкурентных resume→один side-effect, stale-checkpoint→conflict без consume) |
| R3 | 059 | pending | — |
| R4 | 060 | pending | — |
| R5 | 061 | pending (blocked by 057–060) | релиз 0.3.0 |
| R6 | 056b | pending (last) | финальная сверка статусов ↔ факт |

## Не-Goal (отдельно от remediation-релиза)

- **OpenRouter credit-402 clamp** — ветка `fix/openrouter-credit-402`, коммит `5883268`. Отдельное
  логическое изменение, **не** входит в 0.3.0 release identity.

Историческая справка: коммит `62c6ba8` ранее вписал `✅ DONE`/«IMPLEMENTED» прямо в approved-контракт
(+61/−20) — это self-certification, откачено в рамках R0 (см. `UPSTREAM_REMEDIATION_REQUEST.md`,
`REMEDIATION_PLAN.md`).
