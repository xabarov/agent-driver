# U2 — ToolGate call identity + provenance

Дата создания: 2026-08-02. Статус: **IN PROGRESS — фазы A/B/D DONE 2026-08-02; C/E открыты**.
Родитель: [[048-pentestlens-embedding-readiness-goal]]. Происхождение: upstream Goal (host-adoption).

> **Реализация A/B/D** (свип 2889, +16): **A** `ToolGateContext` получил `tool_call_id`+`attempt_id`
> (заполняются в `governed._apply_tool_gate` из `call.tool_call_id` и `attempt_{index}`). **B**
> `GateProvenance(decision_id, policy_snapshot_id, metadata)` + optional `provenance` на
> Allow/Deny/Ask; валидированная provenance сворачивается в `ToolPolicyOutcome.metadata` под
> reserved-ключом `_ad_gate_provenance` (namespace `contracts.validation.RESERVED_METADATA_PREFIX
> = "_ad_"`); на ask-пути пробрасывается в approval-interrupt + его envelope (merge последним →
> host/model не перезапишут). **D** `ensure_bounded_json_metadata` (depth/bytes/keys/reserved-key)
> fail-closed → DENY на non-JSON/oversized/too-deep/reserved-namespace host-metadata (изоляция
> authorship). `GateProvenance` экспортирован из `agent_driver.runtime`. Тесты:
> `tests/runtime/test_tool_gate_provenance.py`, `tests/contracts/test_bounded_metadata.py`.
>
> **interrupt_id-унификация DONE 2026-08-02 (свип 2964, +3):** оба билдера через общий
> `tools/executor/interrupt_ids.py` (`build_interrupt_id`/`build_attempt_id`) → единая схема
> `int_{run_id}_{tool_call_id or index}` (run-scoped + call-stable). Безопасно: resume матчит только
> эхнутый `pending.interrupt.interrupt_id` (никто не реконструирует независимо), HITL-suite цел. Тест
> `tests/runtime/test_interrupt_id_unification.py`.
>
> **Осталось C/E:** сквозное сохранение provenance через event-log/trace/terminal-проекции (в
> envelope/interrupt уже течёт); harness-minted стабильный call-id с write-back на `ToolCall` (None-
> случай); полная retry/timeout/abort-adversarial-матрица; различие gate-DENY vs static-DENY в
> `RuntimeDecision`.

Расширить доменно-нейтральный gate-контракт так, чтобы каждая оценка имела стабильную корреляцию
(`tool_call_id`, `attempt_id`, run/session id, optional host-metadata), а каждое `allow`/`deny`/
`ask` могло нести optional JSON-safe **provenance** (`decision_id`, `policy_snapshot_id`, generic
metadata). Рантайм обязан пронести валидированную provenance **точно** через каждый применимый
interrupt/checkpoint/event/envelope/trace/resume/terminal и **не давать** модели/тулу авторить или
перезаписывать host-provenance. Non-JSON/oversized/malformed/reserved-key/inconsistent →
fail-closed.

## Что уже есть (не переделываем)

- **Gate-контракт** — `runtime/tool_gate.py`: `ToolGateContext` (L126) с `tool_name`, `args`,
  `run_id`, `thread_id`, `agent_id`, `risk`, `side_effect`, `current_tool_calls`; результаты
  `ToolGateAllow`/`ToolGateDeny`/`ToolGateAsk` (frozen dataclass); `ToolGate = Callable[[ctx],
  Awaitable[result]]`. Fail-closed на исключении gate (`governed.py:398-410`).
- **Провайдер-supplied `tool_call_id`** — `ToolCall.tool_call_id` (`contracts/tools/calls.py:18`),
  заполняется из tool-call `id` провайдера (`llm/tool_call_parser.py:142`); коллизии переименовывает
  `dedupe_tool_call_ids` (`tools/executor/planned.py:10`).
- **`ToolPolicyOutcome.metadata: dict`** (`contracts/tools/policy.py:63`) — generic metadata-слот
  на исходе уже есть (но gate в него не пишет — см. gaps).
- **Проекции решений**: `_emit_tool_policy_runtime_decisions`
  (`tool_stage/__init__.py:685`) → `RuntimeDecision` (`contracts/runtime_decisions.py:105`) с
  `decision_id=f"dec_{uuid4().hex}"`, `redacted_metadata` (несёт `tool_call_id`); event
  `TOOL_CALL_STARTED` несёт `tool_call_id` per call; interrupt-envelope сохраняется в checkpoint.
- **JSON-serializable-валидатор** — `contracts/validation.py:9 ensure_json_serializable`
  (`json.dumps`-round-trip) уже навешан на многие `metadata`-поля.
- Тесты: `tests/runtime/test_tool_gate.py`, `tests/sdk/test_tool_gate_e2e.py`,
  `tests/permissions/test_gate.py`, `tests/tools/test_policy_interrupt_title.py`.

## Незакрытые gaps (этот эпик)

1. **`ToolGateContext` не несёт call-identity**: нет `tool_call_id` (есть на `ToolCall`, но
   `_apply_tool_gate` в `governed.py:386-395` его не копирует), нет `attempt_id`, нет session-id
   сверх `run_id`/`thread_id`. Gate не может привязать внешнее policy-решение к конкретной попытке.
2. **У результатов gate нет provenance-канала**: ни `decision_id`, ни `policy_snapshot_id`, ни
   generic `metadata` на `ToolGateAllow/Deny/Ask`. `reason` — телеметрия; `title` уходит в heading.
   `_apply_tool_gate` **никогда** не пишет gate-metadata в `ToolPolicyOutcome.metadata` → канала
   gate→outcome→envelope нет.
3. **Нет harness-minted стабильного planned-call-id сквозь lifecycle**. Provider-`tool_call_id`
   может быть `None`; позиционные fallback'и (`call_{index}`, `{run_id}:tool:{index}`) минтятся
   по-разному на разных границах и НЕ пишутся обратно на `ToolCall`. Resume коррелирует по
   `interrupt_id`, который выводится **непоследовательно**: `policy_interrupt.py:21` index-based
   (`int_{run_id}_{index}`, `attempt_{index}`) vs `allowed.py:387/110` call-id-based
   (`int_{tool_call_id or index}`). `attempt_id` всюду = `attempt_{batch_index}`, НЕ per-retry.
4. **Валидация метаданных минимальна**: `ensure_json_serializable` — только `json.dumps`, без
   depth/size/byte-limit/reserved-key/recursion-guard. Нет изоляции host-vs-model: merge плоский
   `{..., **run_metadata}` (`policy_interrupt.py:51-54,69-72`) молча перезаписывает ключи. Ничто
   не мешает выводу тула заполнить те же ключи, что использует хост.
5. **Gate-DENY неотличим от static-policy-DENY** в `RuntimeDecision`-проекции (`reason=
   "tool_policy_denied"` в обоих случаях); собственный gate-reason живёт только в
   `envelope.error`/`policy.reason`. `decision_id` минтится на **projection-time**, не связан с
   gate-решением.
6. **Тест-дыры**: нет полного resume/approve-после-ask e2e; нет retry/attempt-id-корреляции; нет
   gate-timeout; нет abort-during-gate; нет provenance-round-trip (канала нет).

## Фазы

A. **Call-identity в `ToolGateContext`**: добавить `tool_call_id` (harness-minted стабильный, если
   провайдер дал `None` — синтезировать один раз и **записать обратно** на `ToolCall`), `attempt_id`
   (per-execution/retry, не batch-index), session-id. Унифицировать `interrupt_id`/`attempt_id`-
   деривацию (один builder; убрать index-vs-call-id-расхождение `policy_interrupt.py` ↔ `allowed.py`).
   Resume коррелировать по стабильному call-id, не только `interrupt_id`.
B. **Provenance-канал на результатах gate**: добавить optional JSON-safe `decision_id`,
   `policy_snapshot_id`, generic `metadata` на `ToolGateAllow/Deny/Ask`; `_apply_tool_gate`
   переносит их в `ToolPolicyOutcome.metadata` под **зарезервированным host-namespace**.
C. **Сквозное сохранение**: провести валидированную provenance через interrupt → checkpoint →
   event → tool-result-envelope → trace/RuntimeDecision → resume → terminal **без потери и без
   перезаписи**. Различать gate-DENY от static-DENY в проекции.
D. **Fail-closed валидация**: заменить голый `ensure_json_serializable` на bounded-валидатор
   (max depth/size/bytes/keys, reserved-key-conflict, deterministic serialize+hash). Изоляция
   authorship: host-provenance в отдельном namespace, model/tool не могут в него писать; плоские
   `**`-merge заменить на namespaced-merge с collision-guard.
E. **Adversarial-тесты** (матрица §acceptance-3): allow/deny/ask/resume/retry/failure/timeout/abort
   — assert стабильность call-id, смену attempt-id где надо, точное сохранение provenance,
   redaction-safe trace, отсутствие дублей/противоречий identity, fail-closed на malformed/oversized/
   reserved-key/model-overwrite. Приёмка: свип, CHANGELOG, ledger, схема+лимиты в handoff.

## Не в скоупе

- Смысл provenance (что такое policy_snapshot) — хост; движок носит непрозрачные ID/bytes.
- Реальный gate-timeout-enforcement сверх текущего контракта («gate владеет своим timeout»,
  `tool_gate.py:48-55`) — если понадобится, отдельная фаза; здесь только тест-покрытие.
