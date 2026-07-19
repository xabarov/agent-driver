# Refactoring pass после серии эпиков 015-022

Дата создания: 2026-07-19. Статус: **done (первый проход, 2026-07-19)** — батчи 1-4 и 7
выполнены; 5-6 отложены (см. ниже).

> Итог: (1) guard-плоскость выделена в `tool_stage/guards.py` (~500 строк: failure-streak,
> no-progress policy, force-final controls/budgets; `__init__` реэкспортирует — все внешние
> импорты и тесты живы, `__init__` 1776→1290 строк); (2) единый предикат
> `final_content_unusable` в answer_recovery — обе точки лестницы (entry-gate stream_recovery
> и `_unusable` в completion) делегируют ему; (3) `provider_model_hint`: явный opt-in протокол
> `model_hint()` у провайдера/обёртки приоритетнее атрибутного unwrap (+тест); (4) memory:
> общий `sync_raw_turn` вместо дублирования в StoreBacked/Fact-fallback; (5) config_sections:
> mid-file import поднят наверх. Полная регрессия зелёная; полный strategy-list рерайт
> лестницы НЕ делался осознанно (шаги уже именованные функции, риск > выгода без изменения
> поведения). Отложено: генерация metadata-инвентаря из реестра (5), консолидация
> test-fixtures в conftest (6), каталог окон в data-файл — кандидаты следующего прохода.
> Замечание: репозиторий НЕ ruff-format-clean целиком (~160 файлов дрейфа) — массовое
> форматирование намеренно не коммитилось; отдельным решением владельца.
Правило: без изменения поведения; каждый батч — полная регрессия
(runtime/context/llm/subagents/contracts/memory) зелёная до и после.

## Кандидаты

1. **Recovery-ladder в `llm_step/`** (stream_recovery + completion): за 015-018 лестница
   forced-final обросла ветками (non-stream → no-tools → history-fold → fallback-provider →
   prior-turn → empty-signal, плюс degenerate-refusal-гейты в двух местах). Вынести в
   явный упорядоченный список стратегий с единым `_unusable()`-предикатом; шаги лестницы —
   маленькие функции с одинаковой сигнатурой.
2. **`context_windows.py`**: `provider_model_hint` unwrap по списку атрибутов — заменить на
   опциональный протокол (`provider_model_hint()` у обёртки провайдера) с сохранением
   атрибутного фолбэка; каталог окон → data-файл.
3. **`tool_stage/__init__.py`** разросся (guards: loop, failure-streak, refund, budgets) —
   выделить `tool_stage/guards.py`.
4. **`memory/`** теперь 3 провайдера + сторы: развести `provider.py` (контракты) от
   `providers.py` (реализации), extraction — уже отдельно; общий helper raw-turn sync
   (дублирован в StoreBacked и Fact fallback).
5. **Метадата-инвентарь**: ключей стало много (docs/runtime-metadata.md строки-простыни) —
   генерация таблицы из декларативного реестра констант вместо строковых литералов по коду
   (константы уже есть местами; свести).
6. **Тесты**: tests/memory и tests/runtime перекрёстно тестируют hook-и; сгруппировать
   fixtures (FakeProvider-вариации повторяются в 5+ файлах) в conftest.
7. **Слоистость слот-датаклассов** (`config_sections.py`): резолверы (`resolved_for_model`,
   `for_context_window`) накапливаются — вынести резолв-логику из датаклассов в функции
   модуля (датаклассы — чистые данные); заодно убрать ловушку `type(self).attr` (см. урок
   эпика 017 о member descriptor).

## Порядок

Батчи 1-2 (горячие пути) → 3-4 → 5-7. Каждый — отдельный коммит с пометкой `refactor:`;
хост-пин MeetScript бампится один раз в конце с прогоном обоих бенч-сабсетов.
