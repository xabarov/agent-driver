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
| U2 | ToolGate identity + provenance | 050 | **DONE** | interrupt/envelope + terminal/trace-проекция + adversarial-matrix закрыты R1/эпиком 057 |
| U3 | Atomic approval + resume | 051 | **IN PROGRESS** | SQLite/in-mem CAS есть; **Postgres durable — R2/эпик 058** |
| U4 | Durable Stop + cancellation | 052 | **DONE** | код + deadline в release-SHA (0.3.0); полная матрица закрыта R4/эпиком 060 |
| U5 | Plan integrity extension point | 053 | **DONE** | hash+enforcement+PlanArtifact + trace projection закрыты R3/эпиком 059 |
| U6 | Gateway truthfulness | 054 | **DONE** | process-local/non-durable + fail-fast (Option 2) — принято аудитом как допустимое |
| U7 | Version/release/handoff | 055 | **SUPERSEDED** | `0.2.0` не переиспользуется; корректный релиз — 0.3.0/R5/эпик 061 |

**Зонтичный эпик 048:** ✅ **COMPLETE** — все acceptance R0–R6 выполнены; релиз 0.3.0 несёт весь required-код
в одном SHA, wheel воспроизводим, handoff с проверяемыми receipts. Клиент проверяет по неизменённому
`upstream-requirements.md` (SHA `d4ed6c…`).

## Remediation (R0–R6) — статус

| R | Эпик | Статус | Что сделано |
|---|---|---|---|
| R0 | 056a | **DONE** | контракт восстановлен до SHA `d4ed6c…`; статусы вынесены сюда |
| R1 | 057 | **DONE** | `_ad_gate_decision`-маркер + provenance на envelope (allow/deny/ask) + terminal `runtime_decision`-проекция (gate≠static через `policy_id`/`trigger`) + redaction-safe `redacted_metadata` + stable identity + fail-closed; 7 тестов `test_provenance_lifecycle.py` |
| R2 | 058 | **DONE** | 4 PG-стора + generic schema + facade + обязательный CI-job + **20 real-PG тестов** (validated на postgres:15): 17 store-unit (two-client race, replay, conflict, монотонный abort-CAS, restart, parity) + 3 resume-integration end-to-end (duplicate→conflict, два конкурентных resume→один side-effect, stale-checkpoint→conflict без consume) |
| R3 | 059 | **DONE** | `policy_binding`+`approved_by` в `PLAN_APPROVED/REJECTED` trace-событии (из authoritative approved_plan) + real checkpoint readback + unforgeable + EDIT re-hash bugfix; 4 теста `test_plan_binding_trace.py` |
| R4 | 060 | **DONE** | код в ветке (d43720d+7bf1c6d — ancestor) → в release-SHA; матрица-маппинг (cell→test) в эпике 060 + добор `test_u4_stop_matrix.py` (abort-before-planning, fenced-late-result-не-воскрешает) |
| R5 | 061 | **DONE** | 0.3.0 bump (pyproject/`__version__`/METADATA/changelog согласованы); reproducible wheel `13a6a709…465db` (2 изолированные сборки идентичны); handoff `handoff-0.3.0-pentestlens-remediation.md` с receipts (full-suite 2982 + real-PG 20 на PG15.18) |
| R6 | 056b | **DONE** | эпики 048–055 + U-таблица выше сведены с фактом; 048 → complete |

## Не-Goal (отдельно от remediation-релиза)

- **OpenRouter credit-402 clamp** — ветка `fix/openrouter-credit-402`, коммит `5883268`. Отдельное
  логическое изменение, **не** входит в 0.3.0 release identity.

Историческая справка: коммит `62c6ba8` ранее вписал `✅ DONE`/«IMPLEMENTED» прямо в approved-контракт
(+61/−20) — это self-certification, откачено в рамках R0 (см. `UPSTREAM_REMEDIATION_REQUEST.md`,
`REMEDIATION_PLAN.md`).
