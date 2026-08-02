# F3 — Monotonic checkpoint revision foundation

Дата: 2026-08-02. Статус: **Вариант A РЕАЛИЗОВАН 2026-08-02 (свип 2960, +3); Вариант B (ordering-rewire)
отложен**. Разблокировал: revision-based optimistic-concurrency (U3). Связано:
[[051-atomic-approval-resume]].

> **Реализован Вариант A (additive):** `CheckpointRef.revision: int = 0`; вывод в
> `build_checkpoint_ref` = `(chain.previous_row.ref.revision + 1) if previous_row else 0` (без доп.
> чтений БД). Consumer: `ResumeCommand.expected_revision` + guard в `_handle_resume_with_pending`
> (mismatch → `ResumeConflictError`, mirror `expected_checkpoint_id`). `latest()`-ordering НЕ тронут →
> conformance-тесты целы; schema-snapshot ResumeCommand обновлён. Тесты:
> `tests/runtime/test_checkpoint_revision.py` (revision 0→1→2+parent-chain; expected_revision
> mismatch→conflict, match→approve). **Осталось (Вариант B, отложено):** перевести `latest()` всех 4
> сторов на `revision DESC` (+ Postgres-колонка/индекс) — низкая марж. ценность, микросекундный тай
> латентен; делать после доказанной необходимости.

## Зачем

`CheckpointRef.state_version` захардкожен `"v1"` (мёртвая семантика). Каждый `save()` минтит новый
`chk_{uuid4().hex}` → `ON CONFLICT/INSERT OR REPLACE` никогда не контендит; `latest()` в 4 сторах
упорядочивает **по-разному** и имеет микросекундные тай-риски. Монотонный per-run `revision` даёт
единый ordering-инвариант и опору для «expected-revision» (U3).

## Текущее состояние (grounded)

| Стор | `save()` keying | `latest()` ordering | Тай-риск |
|---|---|---|---|
| InMemory (`runtime/checkpoints.py:65`) | list `_by_run[run_id]` | `rows[-1]` (insertion) | нет (порядок вставки) |
| Sqlite (`runtime/sqlite_store.py:30`) | PK `checkpoint_id`, нет seq/created_at-колонки | `ORDER BY json_extract(payload,'$.checkpoint.created_at') DESC` | **да** (одна микросекунда) |
| Jsonl (`runtime/storage/jsonl_store.py:275`) | append-строка | последняя строка файла | нет (append) |
| Postgres (`postgres_store.py:73`) | PK `checkpoint_id`, DB-колонка `created_at NOW()` | `ORDER BY created_at DESC` | **да** (NOW() тики) |

- `CheckpointRef` (`contracts/checkpoints.py:13-33`): поля incl. `state_version: str`,
  `parent_checkpoint_id`; **нет** `revision`/`seq`.
- `build_checkpoint_ref` (`runtime/checkpoint_factory.py:33-56`): единственная точка минта;
  `state_version="v1"`; **уже получает `chain.previous_row`** → revision выводим без доп. чтений БД.
- Конформанс-тесты (`test_storage_conformance.py:53-74`, `test_runtime_stores.py:17-36`): опираются на
  `latest()==второй save` + parent-chain `second.parent==first`. Тестов, требующих `state_version=="v1"`
  как ВЫХОД, **нет** (все — фикстуры на вход).
- Единственный прод-читатель `state_version`: `harness/durable_lifecycle.py:145`.

## Design decision

**Добавить `revision: int` на `CheckpointRef`, вывести в `build_checkpoint_ref` как
`(chain.previous_row.ref.revision + 1) if chain.previous_row else 0`.**

Ключевая развилка ДО кода — **два варианта**:
- **Вариант A (additive, низкий риск):** `revision` — просто поле; `latest()` НЕ меняем. Revision
  доступен для сравнения/optimistic-concurrency, но ordering остаётся прежним. Конформанс-тесты не
  трогаем. **Рекомендую как первый шаг.**
- **Вариант B (ordering-rewire, средний риск):** `latest()` всех 4 сторов упорядочивает по
  `revision DESC` (детерминизм, убирает микросекундный тай). Требует: Sqlite —
  `json_extract('$.checkpoint.revision')`; Postgres — новая колонка `revision` + индекс; Jsonl/InMemory
  — тривиально. Риск: `test_storage_conformance` завязан на `latest`; менять поведение осторожно
  (проверить, что revision-order == текущий insertion-order на существующих кейсах).

Решить: делаем **A сейчас** (разблокирует expected-revision), **B — отдельно** после доказанной
необходимости (микросекундный тай — латентный, не наблюдённый инцидент → правило promotion ledger'а
советует ждать боли).

## Код-сайты

- `contracts/checkpoints.py` — `revision: int = 0` на `CheckpointRef` (additive, default).
- `runtime/checkpoint_factory.py` — вывод revision из `chain.previous_row`.
- (Вариант B) 4 стора `latest()` + Postgres-миграция колонки/индекса.
- `state_version` — оставить (отдельная семантика durable-lifecycle) ИЛИ задепрекейтить в пользу
  revision — решить; `harness/durable_lifecycle.py:145,575,640,730` — единственные писатели/читатели.

## Тест-план

- `build_checkpoint_ref`: последовательные save → revision 0,1,2…; parent-chain сохранён.
- Два save «в одну микросекунду» (мок времени) → revision-tiebreaker различает (Вариант B).
- Конформанс: `latest()` и parent-chain не сломаны (Вариант A — по построению; Вариант B — явный
  тест revision-order == insertion-order).
- expected-revision (U3): resume с `expected_revision`, не совпадающим с текущим → стабильный
  conflict (надстройка на F3, отдельный эпик-шаг).

## Риски / митигидии

- **Postgres-миграция** (Вариант B) — новая колонка, версия схемы; делать как v3-миграцию рядом с
  `runtime_events` unique-idx.
- **Смена `latest()`-семантики** — только Вариант B, за отдельным коммитом + явным конформанс-тестом.
- **Двойной источник версии** (`state_version` vs `revision`) — задокументировать роли: `state_version`
  = формат сериализации, `revision` = позиция в цепочке рана.

## Не в скоупе

expected-revision resume-guard (надстройка U3 после F3); полный branch/fork-checkpoint versioning.
