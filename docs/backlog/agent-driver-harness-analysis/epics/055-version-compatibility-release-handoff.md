# U7 — Version, compatibility, release handoff

Дата создания: 2026-08-02. Статус: **DONE 2026-08-02** — релиз `0.2.0` нарезан. Родитель:
[[048-pentestlens-embedding-readiness-goal]]. Происхождение: upstream Goal (host-adoption).

> **Release cut** (свип 2965): версия `0.1.0`→**`0.2.0`** (minor-bump, additive/backward-compat;
> `pyproject`==runtime `__version__`==dist-metadata, guard `test_version.py`); CHANGELOG `[Unreleased]`
> → `[0.2.0] - 2026-08-02`. **Wheel** `agent_driver-0.2.0-py3-none-any.whl`, **SHA-256**
> `f03fad0d3c5c2883c0e76fdd073b83c8287ec28343731c493938b0561367a121`, **byte-for-byte воспроизводим**
> (2 изолированные сборки с `SOURCE_DATE_EPOCH`). **Handoff-документ:**
> `handoff-0.2.0-pentestlens.md` (release-identity, facade-манифест, контракты U1-U7, тест-команды,
> wheel+SHA, migration/compat, остаточные риски). Migration: всё additive + persisted-compat.

> **Реализация** (свип 2907, +3): `agent_driver.__version__` (из `importlib.metadata` с
> pyproject-fallback), guard-тест `tests/test_version.py` (совпадает с `pyproject [project] version`,
> валидная pre-1.0). CHANGELOG compat-нота: весь Unreleased-набор additive + persisted-state-
> совместим (новые optional-поля `None`, новые metadata-ключи additive, старые checkpoint/event
> читаются, ни один публичный символ не удалён/переименован).
>
> **Осталось (release-действие, gated на прохождении всего Goal — U1 и углубления ещё открыты):**
> выбрать следующую валидную pre-1.0-версию по upstream-истории (НЕ хардкодить); согласовать
> metadata/wheel/changelog; чистая детерминированная wheel-сборка + точный filename+SHA-256 (при
> обещании reproducibility — две изолированные сборки); upstream handoff-документ (base+final commit,
> facade-манифест символов, migration, wheel-hash, Python-версии/extras, остаточные риски).

Выбрать следующую семантически валидную pre-1.0-версию **после** прохождения всех контрактов U1–U6.
Согласовать package-metadata / runtime `__version__` / wheel-filename+metadata / docs / changelog.
Прогнать чистую детерминированную wheel-сборку на supported-Python, записать точный filename+SHA-256
(при обещанной byte-for-byte воспроизводимости — доказать двумя изолированными сборками). Закоммитить
всё, оставить worktree чистым; tag welcome при соответствии upstream-release-policy, но **полный
commit-SHA обязателен** для PentestLens. Задокументировать миграцию старых internal-импортов → facade
и любые persisted-checkpoint/event-compat-ограничения.

## Что уже есть (не переделываем)

- `pyproject.toml`: `version = "0.1.0"`, name `agent-driver`, `requires-python >=3.11`; optional-extras
  (`dev`/`postgres`/`cli`/`instructor`/`acp`/`server`/`pdf`) объявлены; `py.typed` ship'ится
  (проверяется `tests/test_public_api.py`).
- `CHANGELOG.md` с секцией `[Unreleased]` (уже ведётся по-фиксно).
- Guard-тесты публичной поверхности (`test_public_api.py`, `contracts/test_public_exports.py`) —
  база для compat-снапшота (расширяется в U1).

## Незакрытые gaps (этот эпик)

1. **Нет runtime `__version__`** — grep по `agent_driver/` пуст; embedder не может проверить версию
   в рантайме. (Общая фаза с U1-B: single-source версии.)
2. **Версия не выбрана/не согласована** — `0.1.0` в `pyproject`; нужно выбрать следующую валидную
   pre-1.0 **по upstream-истории** (НЕ хардкодить `0.2.0rc6` только потому, что PentestLens юзал
   старый rc5); согласовать metadata/`__version__`/wheel/docs/changelog.
3. **Нет детерминированной сборки + hash-evidence** — нужен чистый isolated wheel-build на
   supported-Python, точный filename + SHA-256; при обещании воспроизводимости — две изолированные
   сборки.
4. **Нет migration-notes** — старые internal-импорты → supported-facade (из U1) + persisted-
   checkpoint/event-compat-ограничения (особенно после revision-изменений checkpoint в U3).

## Фазы

A. **Версия single-source**: добавить `agent_driver.__version__`, синхронизированный с `pyproject`
   (тест на согласованность). Выбрать следующую валидную pre-1.0 по upstream-истории после зелёных
   U1–U6.
B. **Согласование**: package-metadata / `__version__` / wheel-filename+metadata / docs / changelog
   совпадают. `[Unreleased]` → версионная секция.
C. **Детерминированная сборка**: чистый isolated build на supported-Python; записать точный
   wheel-filename + SHA-256; при обещанной reproducibility — повтор двумя изолированными сборками,
   доказать byte-for-byte.
D. **Migration + compat**: migration-notes (internal-импорты → facade из U1; persisted-state-
   ограничения); breaking/deprecation-заметки. Приёмка (§acceptance-1,9): baseline+финальные
   unit/lint/type/docs-гейты зелёные без required skip/xfail; supported-Python-matrix; worktree чист.
E. **Handoff-документ**: собрать required upstream handoff (base+final commit, clean-status, версия,
   facade-манифест символов, breaking/deprecation+migration, точный wheel-filename+SHA-256+repro,
   Python-версии/extras, остаточные риски). Без секретов/host-путей/env-дампов.

## Не в скоупе

- Провайдер/модель-selection для PentestLens.
- Публикация в индекс/дистрибуция — только evidence (commit-SHA + wheel + hash) для пина хостом.
- Tag как обязательство (welcome, но commit-SHA — источник истины).
