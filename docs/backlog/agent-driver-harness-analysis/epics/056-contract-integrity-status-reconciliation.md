# R0+R6 — Contract integrity & status reconciliation

Статус: **056a DONE / 056b pending (последним)**
Отвечает на: `UPSTREAM_REMEDIATION_REQUEST.md` R0, R6. Полный контекст: `../REMEDIATION_PLAN.md`.

## Проблема

Коммит `62c6ba8` правил approved-input `upstream-requirements.md` in-place (+61/−20), вписав `✅ DONE`/
«IMPLEMENTED» прямо в требования → self-certification. Входной контракт — граница между репозиториями;
исполняющий агент не может переписывать acceptance под уже сделанное.

## 056a — восстановление контракта (DONE)

- `upstream-requirements.md` восстановлен из `62c6ba8~1` → SHA
  `d4ed6c371eda50e6c0b7fa07df55974cfac7411e32a95708a3f203cbcd526316` (==approved). Байт-идентичен, чтобы
  downstream проверял `sha256sum` напрямую; правило неизменяемости вынесено в `../status-ledger.md`, не в сам файл.
- Статус/evidence перенесены в `../status-ledger.md` (авторитетный источник статуса).

**DoD R0:** ✅ approved SHA однозначен; ✅ нет self-cert `DONE` в контракте; ✅ отдельные доки
(status-ledger + handoff) ссылаются, не меняя смысл.

## 056b — финальная сверка (pending, после R1–R5)

- Привести `epics/048–055` к фактическому terminal-статусу; ни один обязательный пункт ≠ `optional`.
- `capability-ledger.md` + handoff называют одинаковые остаточные риски; убрать ложные заявления о
  Postgres/trace/release-содержимом.
- Закрыть 048 aggregate-DoD ссылками на реальные тесты/коммиты. Затем 048 → `complete`.

**DoD R6:** статусы согласованы; obligatory ≠ optional; нет ложных заявлений о содержимом релиза.
