# Structured-output контракт для headless-хостов: схема-валидированный финал через тул

Дата создания: 2026-07-23. Статус: **DONE (A-D)** 2026-07-23 (из фиче-скана референсов 2026-07-23; номер 031→036 при консолидации 2026-07-23 — коллизия двух раунд-2 сканов).

## ⚠️ Live-находка (2026-07-23, из эпика 038) — ПРОВЕРИТЬ

Форсированный tool-call `structured_completion` **не даёт tool-call вживую** через
MeetScript privacy-провайдер + OpenRouter для `google/gemini-2.5-flash-lite`
(`StructuredOutputError: no emit_result tool call in the response`). Найдено при первом
живом использовании (эпик 038 — генерация подсказок; переведена на плоский completion).
Следствие для этого эпика: **memory/extraction (фаза B) использует тот же канал** и на
проде, вероятно, тихо падает в raw-fallback (его `except` глушит) — то есть контрактная
экстракция может НЕ работать вживую на этом провайдере. Требует проверки: (а) поддерживает
ли openai-compat адаптер + OpenRouter форсированный `tool_choice` для не-Anthropic моделей;
(б) заполняется ли `planned_tool_calls` в метаданных ответа для gemini/deepseek. Кандидат в
бэклог: либо чинить плумбинг tool_choice в провайдере, либо дать structured_completion
плоский JSON-fallback (как поступил эпик 038 для best-effort).

## Итог (2026-07-23)

- **A. Механизм.** `agent_driver/llm/structured.py`: `structured_completion(*, provider, messages, schema, ...)` — форсит `tool_choice` на динамический тул `emit_result` со схемой в параметрах; читает `planned_tool_calls` из метаданных ответа, валидирует required-ключи + типы (`_validate`), при mismatch/no-tool-call добавляет корректирующий ход и ретраит, при исчерпании — `StructuredOutputError` (НЕ salvage свободного текста). Экспорт в `agent_driver/llm/__init__.py`. Run-level seam: `AgentRunInput.structured_output: dict | None` — при заданной схеме терминальный answer парсится+валидируется в финализации (`finalization/output.py::_validate_structured_terminal`), результат в `AgentRunOutput.metadata["structured_output"]`, невалидный/пустой финал → `structured_output_error` (сигнал, не молчаливый `completed`). Инертно при `None`. 5+3 теста (`tests/llm/test_structured_output.py`).
- **B. Перевод потребителей.** Extraction фактов памяти (`agent_driver/memory/extraction.py`) переведён с bounded-ретраев свободного JSON (027C) на `structured_completion` со `_EXTRACTION_SCHEMA`; slot-гигиена/dedup/cap сохранены в `_facts_from_structured`. Тесты памяти обновлены на tool-канал, проходят.
- **C. Терминальная дисциплина.** Пустой/непарсибельный structured-финал даёт `structured_output_error` в терминальных метаданных — терминальный сигнал, не `completed`-с-мусором (тест `test_run_level_structured_output_valid_and_invalid`).
- **D. Приёмка.** dd9a5ee-класс закрыт **по построению**: тест `test_never_calls_tool_raises_not_silent` доказывает, что отсутствие tool-call поднимает ошибку, а не salvage-ит прозу. Полный движковый свод: затронутые сюиты (llm/memory/contracts/runtime) зелёные; 3 pre-existing фейла (cli budget, phase6 planning ×2) подтверждены на чистом дереве эпика 032 — не регрессии 036.

Мотивация: во всех местах, где движок или хост ждут от модели МАШИННЫЙ результат, мы
парсим свободный текст с bounded-ретраями (extraction фактов памяти — deepseek-флейк
dd9a5ee/эпик 027; хостовые грейдеры goal-gate; бенч-раннеры). Класс «слабая модель эхает
схему / отдаёт не-JSON» лечится точечно. Референс показывает общий механизм: финальный
структурированный ответ отдаётся ЧЕРЕЗ ТУЛ со схемой — валидация живёт на слое tool-call,
и модель сама ретраит при mismatch, потому что невалидный вызов возвращает ошибку тула.

## Reference-first

- **openclaude `src/tools/SyntheticOutputTool/`** — только в non-interactive сессиях:
  динамический тул с runtime-заданной JSON-схемой (Ajv-валидация); модель обязана вернуть
  финал вызовом этого тула. Гейтится как internal worker-tool в coordinator-режиме.
  (Тот же паттерн — `StructuredOutput` для workflow-субагентов в Claude Code: «validation
  happens at the tool-call layer so the model retries on mismatch».)
- Наш собственный прецедент: MeetScript speaker-rename ушёл от `json_schema`-режима к
  tools-mode extractor именно из-за пустых ответов — подтверждение, что tool-канал
  надёжнее свободного JSON у слабых моделей.

## Эскиз фаз

A. **Механизм**: `structured_output(schema)` в AgentRunInput — движок инжектит динамический
   тул, вызов валидируется по схеме (invalid → ошибка тула → модель чинит сама), результат
   кладётся в терминальный артефакт рана типизированным полем (не парсинг answer-текста).
B. **Перевод потребителей**: extraction фактов памяти (замена bounded-ретраев из 027C на
   контрактный канал), хостовые грейдеры/рубрики, бенч-раннеры со схемой вердикта.
C. Взаимодействие с терминальной дисциплиной 024/015: forced-final при исчерпании бюджета
   должен уметь потребовать structured-вызов; пустой structured-финал = терминальный сигнал
   с severity, не молчаливый `completed`.
D. Приёмка: dd9a5ee-класс закрыт контрактно (тест «непарсибельный финал невозможен по
   построению»); Аргус/полный свип без регрессий.

## Не в скоупе

Интерактивные clarify-каналы (horizon-scan п.9, отложено), формат стриминга финала (SSE
хоста не меняется — structured-канал ортогонален видимому ответу).
