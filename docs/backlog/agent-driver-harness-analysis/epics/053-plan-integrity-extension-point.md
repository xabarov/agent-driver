# U5 — Plan integrity extension point

Дата создания: 2026-08-02. Статус: **IN PROGRESS — ядро A/B/C + enforcement DONE 2026-08-02;
PlanArtifactStore-wiring + trace открыты**. Родитель: [[048-pentestlens-embedding-readiness-goal]].
Происхождение: upstream Goal.

> **Реализация enforcement** (свип 2939, +6): `PlanningPolicyInput.required_plan_hash` — когда задан,
> `_force_planning_has_approved_plan` засчитывает approval только если `approved_plan.content_hash ==
> required_plan_hash`; материально ревизованный план (другой hash) = unapproved → re-gated (DENY) до
> исполнения любого write. Хост, требующий переодобрения при изменении плана, ставит `required_plan_
> hash` = одобренный им hash (пара к harness-авторскому hashing + `plan_policy_binding`). Без него
> (default) — прежнее presence-only (обратно-совместимо). Хелпер `_approved_content_hash`. Тесты:
> `tests/tools/test_plan_hash_enforcement.py` (гейт + evaluator-DENY на ревизии).
>
> **Осталось:** подключить durable `PlanArtifactStore` (всё ещё unwired, гейтящий путь — untyped
> `approved_plan`-dict); провести binding через trace-проекцию.

> **Реализация ядра** (свип 2901, +4): **B** approved-plan content_hash теперь **harness-авторский** —
> `_mark_force_planning_approved` пересчитывает `plan_content_hash(...)` из фактически одобренного
> content (не доверяет model/tool-supplied `content_hash`), а на EDIT — из **edited**-content
> оператора (фикс stale-hash-бага). **A** публичный
> `agent_driver.context.planning.detect_plan_revision(approved_hash, candidate)` — примитив
> детекции материальной ревизии (fail-safe: пустой approved-hash → ревизия). **C** opaque host
> policy-binding (`ResumeCommand.metadata["plan_policy_binding"]`) + `approved_by` переживают в
> `force_planning.approved_plan` и checkpoint; источник — resume-команда (host), модель/тул не
> подделают. Тесты: `tests/runtime/test_plan_integrity.py`.
>
> **Осталось:** подключить enforcement — `_force_planning_has_approved_plan`
> (`tools/policy/evaluator.py`) сравнивает hash, а не только presence (нужен comparison-сайт до
> исполнения); подключить durable `PlanArtifactStore` (всё ещё unwired, гейтящий путь — untyped
> `approved_plan`-dict); провести binding через trace-проекцию.

Сохранить существующую plan-id/content-hash-семантику и выставить supported hook / opaque-metadata-
binding, позволяющий хосту связать одобренную версию плана со своим policy-snapshot/envelope.
**Материальная ревизия плана должна детектироваться до исполнения**, чтобы хост мог потребовать
нового approval. Движок НЕ решает, что есть pentest-policy или materiality; он документирует
extension-point и тестирует, что plan-identity/binding переживает checkpoint/resume/trace и не
перезаписывается model-контентом.

## Что уже есть (не переделываем)

- **Hash-функция** — `context/planning/artifacts.py:20 plan_content_hash(content)` =
  `sha256(content).hexdigest()` (хешит только текст плана).
- **plan_id/content_hash-генерация** — `tools/planning.py:521` (`_exit_plan_mode_v2_tool`:
  `plan_id = args.get("plan_id") or f"plan_{uuid4().hex[:12]}"`; `content_hash =
  plan_content_hash(content)`); `allowed.py:548 _append_plan_approval_interrupt` строит
  `PlanApprovalPayload`.
- **Контракты** — `contracts/context/planning.py`: `PlanArtifact` (L83: `plan_id`, `content_hash`,
  `status`, `approved_at/by`, `metadata`), `PlanApprovalPayload` (L128), `PlanningPolicyInput`
  (L169: `approved`, `approved_plan_id`, `approved_plan`).
- **Store-слой (существует)** — `artifacts.py`: `PlanArtifactStore` (Protocol, L25),
  `InMemoryPlanArtifactStore` (L38), `SqlitePlanArtifactStore` (L63), + `create/update/approve/
  reject_plan_artifact`.
- **Opaque-metadata-слоты** — `PlanArtifact.metadata`, `PlanApprovalPayload.metadata`,
  `InterruptRequest.metadata` (`allowed.py:582`), resume `approved_plan`-dict (`resume.py:71-76`),
  `approved_by`.
- **Trace** — `observability/run_trace/planning.py:44` эмитит `PLAN_ARTIFACT_UPDATED`.

## Незакрытые gaps (этот эпик)

1. **`content_hash` нигде не сравнивается** — вычисляется/переносится/трейсится/хранится, но **ноль
   сравнений/mismatch/verify-сайтов** во всём коде. Gate presence-only: `_force_planning_has_approved_plan`
   (`tools/policy/evaluator.py:46`) True если `approved is True` **или** непустой `approved_plan_id`
   **или** `approved_plan["plan_id"]` — **никогда не смотрит `content_hash`**. → материальная ревизия
   **не детектируется** движком до исполнения.
2. **Модель задаёт и `plan_id`, и `content_hash`** (из `exit_plan_mode`-tool-args,
   `planning.py:521,530`), они пробрасываются verbatim; interrupt/approval-слой **доверяет** hash из
   payload (`allowed.py:554`, не пересчитывает из content).
3. **EDIT-approval переиспользует устаревший hash**: на `ResumeAction.EDIT`
   (`resume.py:252`) `_mark_force_planning_approved` читает `content_hash` из **оригинального**
   pending-payload (`resume.py:60,66-70`), а реально исполняемые args берутся из
   `resume.edited_tool_args` (`apply_resume_to_call`, `resume.py:276-278`). Content меняется на edit,
   а записанный `content_hash` (+`approved=True`) — нет, и не перечекивается.
4. **Durable `PlanArtifact`-модель НЕ подключена**: `PlanArtifactStore`/`create_*`/`approve_*` **не
   упоминаются** в `runtime/`/`sdk/`/`tools/` (ноль call-сайтов вне `context/planning/`). Реально
   гейтящий путь — untyped `context.metadata["approved_plan"]`-dict (`resume.py:54`), не `PlanArtifact`.
5. **Нет авторитетного host-binding-hook'а**: хост может кинуть bytes в `metadata`-dict, но ничто в
   движке не читает host-policy-envelope обратно и не сверяет его с планом на исполнении. Resume-
   checkpoint-linkage — по `interrupt_id`/`run_id`, plan-identity не часть checkpoint-матча. Trace —
   pass-through model-supplied, не авторитетна.

## Фазы

A. **Hash-comparison до исполнения**: движок пересчитывает `content_hash` из фактически исполняемых
   args (включая EDIT-путь) и **сравнивает** с одобренным; mismatch → требование нового approval
   (детектируемо хостом). `_force_planning_has_approved_plan` расширить content-bound-проверкой, не
   только presence.
B. **Защита identity от model-overwrite**: `plan_id`/`content_hash` из одобренной версии — authoritative
   (harness-controlled), model-supplied значения не могут их переопределить через checkpoint/resume/
   trace. EDIT перечитывает и переутверждает hash.
C. **Supported host-binding extension-point**: документированный hook / opaque-metadata-слот (переиспользуя
   существующие `PlanArtifact.metadata`/`approved_plan`), позволяющий хосту привязать policy-snapshot/
   envelope к одобренной версии плана; binding переживает checkpoint/resume/trace и защищён от
   overwrite. Решить судьбу неподключённого `PlanArtifactStore`: либо подключить как durable-путь
   (заменив untyped `approved_plan`-dict), либо явно deprecate.
D. **Тесты** (§acceptance-7): plan-revision/binding-persistence через checkpoint/resume/trace;
   model/tool overwrite-попытки отклоняются; EDIT-с-изменённым-content триггерит re-approval.
   Приёмка: свип, CHANGELOG, ledger; plan-integrity extension-point в handoff.

## Не в скоупе

- Что такое pentest-policy/materiality — **хост** (движок даёт детектирование ревизии + binding-слот,
  решение о re-approval — хостовое).
- Полноценный durable plan-artifact-workflow сверх минимума, нужного для binding+revision-detection.
