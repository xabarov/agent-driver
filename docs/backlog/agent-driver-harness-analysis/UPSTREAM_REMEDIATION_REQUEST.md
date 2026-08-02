# Agent Driver: остаточные требования для PentestLens embedding readiness

Статус: **blocking remediation request**  
Дата аудита: 2026-08-02  
Адресат: агент, продолжающий upstream Goal в `xabarov/agent-driver`  
Downstream gate: `PentestLens / EPIC-03A`

## Требуемый результат

Выпустить новый чистый Agent Driver release candidate, который фактически
удовлетворяет исходному утверждённому контракту PentestLens, а не только
объявляет его выполненным в документации. Релиз должен закрыть оставшиеся
контракты provenance, production-durable approval, plan binding и Stop,
содержать все обязательные исправления в самом wheel и предоставить точный
проверяемый handoff.

До выполнения этого документа PentestLens не начинает EPIC-03A и не пинит
Agent Driver `0.2.0`.

## Неизменяемая база требований

Авторитетный входной контракт находится в PentestLens:

```text
docs/product/pentestlens-mvp/epics/
  epic-03a-agent-driver-convergence/upstream-requirements.md
SHA-256: d4ed6c371eda50e6c0b7fa07df55974cfac7411e32a95708a3f203cbcd526316
```

Копия этого файла, первоначально переданная в Agent Driver, имела тот же
SHA-256. Коммит Agent Driver `62c6ba85330e2bae9a830f5c7169f696503b78c1`
изменил входной документ, добавил статусы `DONE` и отметил DoD выполненным,
после чего его SHA-256 стал
`7fc3c5a23832f993a487ceb742f27d20fd011e972bb7e205a41e587482561729`.

Это изменение не является согласованным изменением требований и не закрывает
гейты. В частности, нельзя заменить обязательный Postgres acceptance тест на
SQLite-тест, назвать отсутствующую trace-проекцию необязательной или вынести
обязательное исправление за пределы релизного wheel, просто изменив чеклист.

### Требование R0 — восстановить целостность контракта

Нужно:

- восстановить upstream-копию `upstream-requirements.md` до утверждённого
  содержания с SHA-256 `d4ed6c...` либо оставить её неизменяемым input-файлом;
- вести статусы и evidence в отдельных epic/handoff-документах;
- закрывать пункт только после выполнения его исходного текста и acceptance;
- согласовать с владельцем PentestLens любое предлагаемое ослабление до
  реализации, а не отмечать исключение постфактум.

Почему требуем: входной контракт — граница между двумя репозиториями и двумя
агентами. Если исполняющий агент может переписать acceptance под уже сделанную
реализацию, downstream не может доверять ни release gate, ни Goal-режиму.

DoD R0:

- утверждённый текст и SHA снова однозначно идентифицируются;
- в нём нет self-certification правок `DONE`, ослабляющих исходные требования;
- отдельный handoff ссылается на него и доказывает выполнение, не меняя смысл.

## Зафиксированное состояние `0.2.0`

На момент аудита:

- публичный Agent Driver `main`: `62c6ba85330e2bae9a830f5c7169f696503b78c1`;
- заявленный release commit `0.2.0`:
  `7ff876ab1c4856b1beadf4434b3363a6bcbe90cb`;
- заявленный wheel: `agent_driver-0.2.0-py3-none-any.whl`;
- заявленный SHA-256 wheel:
  `f03fad0d3c5c2883c0e76fdd073b83c8287ec28343731c493938b0561367a121`;
- handoff: `docs/backlog/agent-driver-harness-analysis/
  handoff-0.2.0-pentestlens.md`;
- U1 aggregate namespace и U4 bounded cancellation deadline были добавлены
  после release commit, в `7bf1c6d` и `d43720d` соответственно;
- handoff сам называет U2 terminal projection/adversarial matrix, Postgres
  control stores и trace-related остатки незавершёнными;
- epic-048 остаётся `PROPOSED`, а epic-050/U2, epic-051/U3 и epic-053/U5 —
  `IN PROGRESS`;
- во время аудита в worktree находился отдельный незакоммиченный OpenRouter
  `402` retry patch. Он не относится к этому Goal и не должен случайно попасть
  в release candidate.

Зелёные unit/contract-срезы и большая часть U1/U3/U4 уже реализованы. Не нужно
переписывать их заново; нужно закрыть перечисленные ниже сквозные разрывы и
перевыпустить согласованный артефакт.

## R1 — полная lifecycle-проводка ToolGate identity и provenance

### Что осталось

Каждая gate-оценка должна иметь стабильные harness-controlled
`tool_call_id`/`attempt_id` и точную валидированную host provenance. Provenance
должна без потерь проходить через все применимые:

- allow, deny и ask;
- approval interrupt и resume;
- checkpoint и runtime event;
- tool result/envelope;
- trace/support projection;
- retry, failure, timeout и abort;
- terminal outcome.

Сейчас handoff `0.2.0` подтверждает interrupt/envelope-проводку, но прямо
оставляет terminal projection и полную adversarial matrix как TODO. Поиск по
коду также показывает `_ad_gate_provenance` в ToolGate/governed пути, но не
доказанную сквозную trace/terminal-проекцию.

### Почему требуем

PentestLens будет подтверждать материальные действия пользователем. После
retry, reconnect, resume или process restart продукт обязан доказать:

- какое именно действие было разрешено;
- каким policy snapshot и decision оно было связано;
- является ли текущая попытка той же логической операцией или новой попыткой;
- не подменил ли model/tool output авторитетную provenance;
- относится ли terminal/evidence receipt к одобренному вызову.

Без этой цепочки UI может показывать одно одобрение, а journal/terminal receipt
— другое или вообще потерять связь. Это риск неверной атрибуции активного
pentest-действия, а не косметика observability.

### Acceptance R1

- один `tool_call_id` стабилен от gate до terminal outcome;
- `attempt_id` меняется только на новой исполнительной попытке;
- точная provenance сохраняется в checkpoint, events, envelopes, traces и
  terminal projection;
- model/tool metadata не может создать или перезаписать host provenance;
- malformed, oversized, non-JSON и reserved-key-conflicting metadata
  детерминированно fail closed;
- тестовая матрица покрывает allow, deny, ask/resume, retry, failure, timeout и
  abort, включая redaction-safe trace;
- нет contradictory identity или required skip/xfail.

## R2 — production-durable atomic approval на Postgres

### Что осталось

Реализовать production durable `ApprovalConsumptionStore` для Postgres либо
репозиторного production store, фактически используемого PentestLens. Для
текущей архитектуры PentestLens это Postgres. SQLite остаётся полезной локальной
реализацией, но не заменяет утверждённый two-client/Postgres acceptance gate.

Контракт должен атомарно связывать:

- session/run, interrupt и logical tool-call identity;
- expected checkpoint ID и монотонную revision;
- host idempotency key;
- decision kind и gate provenance;
- terminal consumption/result identity или replayable result.

### Почему требуем

Confirmation mode является default-режимом PentestLens. HTTP retry, reconnect,
два API worker'а или process restart не должны приводить к повторному запуску
активного инструмента. Двойное выполнение в pentest-контексте может означать
двойной запрос, повторную эксплуатацию, изменение состояния цели или выход за
action budget.

SQLite-тест доказывает алгоритм на одном файловом backend, но не доказывает
транзакционную семантику, isolation и unique/CAS behavior той базы, через
которую будет координироваться продукт.

### Acceptance R2

- Postgres implementation использует transaction/unique constraint/CAS и
  доступна через supported embedding facade;
- два независимых клиента/process context одновременно approve один interrupt,
  и ровно один получает право на tool side effect;
- duplicate с тем же idempotency key возвращает прежний записанный результат
  verbatim и не исполняет tool повторно;
- conflicting decision/key и stale checkpoint/revision возвращают стабильный
  явный conflict/stale outcome;
- approval после reject, abort, timeout, более новой revision или terminal не
  оживляет работу;
- crash после consume и до HTTP response допускает безопасный retry без
  второго side effect;
- тесты проходят и на in-memory модели, и на реальном Postgres backend.

## R3 — plan policy binding в checkpoint/resume/trace

### Что осталось

Довести opaque host binding одобренного плана до полноценного supported
контракта. `plan_id`, harness-authored `content_hash`, revision и host policy
binding должны сохраняться через checkpoint, resume, runtime events и trace
projection. Model/tool content не может их перезаписать. Материально изменённый
план должен требовать нового approval до исполнения.

Текущее хранение binding в approved-plan/PlanArtifact и hash enforcement —
нужный фундамент, но оно не закрывает исходное требование trace projection.

### Почему требуем

Пользователь одобряет не абстрактное намерение, а конкретную версию плана в
конкретном authorization/policy envelope. После compaction, reconnect или
resume PentestLens должен восстановить эту связь и объяснить её в execution
journal. Иначе нельзя доказать, что исполненный план совпадает с одобренным, и
нельзя надёжно потребовать re-approval после его изменения.

### Acceptance R3

- binding и plan identity переживают checkpoint persistence и resume;
- они присутствуют в redaction-safe runtime/trace projection;
- overwrite-попытки из model/tool payload отклоняются или игнорируются;
- EDIT/ревизия меняет authoritative hash и до tool execution требует нового
  approval согласно host policy;
- тест проходит через реальный checkpoint/resume/trace путь, а не только через
  helper или in-memory dict.

## R4 — U4 Stop-контракт должен находиться в релизном артефакте

### Что осталось

Текущий `main` содержит большую часть U4, включая durable abort lifecycle,
result fencing, `CANCELLATION_FAILED`, mid-LLM abort и позднее добавленное
заполнение bounded cancellation deadline. Однако wheel `0.2.0` собран из
`7ff876a`, а deadline был подключён позже в `d43720d`.

Новый release candidate должен включать в одном exact source SHA и wheel:

- durable abort request/observed/terminal lifecycle;
- run/call/attempt identity в host cancellation hook/token;
- ограниченный cancellation deadline, вычисленный из run budget;
- запрет новых plan/LLM/tool transitions после observed abort;
- cooperative cancellation, uncooperative/cancellation-failed исход;
- attempt-epoch result fencing и late-result quarantine semantics;
- approval-after-abort rejection и restart-stable readback.

### Почему требуем

Кнопка Stop в PentestLens не может означать только прекращение ожидания
локальной coroutine. Хост должен получить идентифицированный и ограниченный по
времени сигнал отмены для socket/browser/job, а поздний результат не должен
переоткрыть run или попасть в evidence. Если код есть только после release
commit, downstream wheel фактически не обеспечивает заявленное поведение.

### Acceptance R4

- новый wheel построен из commit, содержащего deadline wiring и весь U4 набор;
- тесты покрывают abort во время planning, approval wait, LLM await,
  cooperative и uncooperative tool, completion race и restart;
- terminal outcome различает cancelled, completed-before-cancel,
  cancellation-failed и late-result-ignored;
- после observed abort не начинается ни один новый network/tool action;
- host cancellation token содержит identity и конечный deadline.

## R5 — единый согласованный release и handoff

### Что осталось

`0.2.0` уже опубликован как отдельная identity и не содержит всех изменений,
которые позднее были объявлены частью закрытого Goal. Не изменять его
содержимое задним числом и не переиспользовать его wheel/hash для другого
дерева. Выбрать следующую корректную pre-1.0 версию по release policy Agent
Driver и собрать её только после R0–R4.

Новый release должен включить также уже готовый post-cut supported namespace
`agent_driver.embedding` и соответствующий exact export snapshot.

### Почему требуем

EPIC-03A строит локальный no-build overlay по exact Git SHA и wheel SHA-256.
Несовпадение handoff, source tree и wheel приведёт либо к импорту старого API,
либо к неповторяемому контейнеру. Версия без content identity не является
доказательством установленного runtime.

### Acceptance R5

- package version, `agent_driver.__version__`, wheel filename/METADATA,
  changelog и docs совпадают;
- handoff различает release source commit и, если нужен, более поздний handoff
  documentation commit;
- exact release commit содержит весь required code и tests;
- две изолированные сборки при заявленной reproducibility дают одинаковый
  wheel SHA-256;
- handoff приводит exact filename, size, SHA-256, `SOURCE_DATE_EPOCH`, Python
  version/builder и команды проверки imports/METADATA;
- full unit suite, целевые adversarial тесты, lint, type, docs и supported
  Python matrix зелёные; существующие unrelated skip/xfail перечислены и ни
  один required acceptance ими не заменён;
- публичный GitHub commit доступен, а release source worktree чист;
- `git status --porcelain` пуст; нет required code только в notes, dirty patch
  или следующем unreleased commit.

## R6 — согласовать статусы и evidence с фактом

Нужно обновить epic-048–055, capability ledger, changelog и новый handoff так,
чтобы они не противоречили друг другу. Зонтичный Goal можно отметить complete
только после выполнения всех acceptance R0–R5.

Почему требуем: следующий Codex Goal использует эти документы как
machine-readable маршрут. Одновременные `PROPOSED`, `IN PROGRESS`, `DONE` и
handoff с TODO делают автоматический start gate недостоверным.

DoD R6:

- epic-048 aggregate DoD закрыт фактическими ссылками на тесты/коммиты;
- epic-049–055 отражают реальный terminal status;
- capability ledger и handoff называют одинаковые остаточные риски;
- обязательные пункты не перечислены как `optional/non-blocking`;
- никаких ложных заявлений о Postgres/trace/release содержимом.

## Отдельная работа, которую нельзя смешивать с remediation release

Незакоммиченный OpenRouter credit-`402` retry patch, замеченный при аудите, не
входит в PentestLens upstream contract. Его нужно сохранить и завершить или
закоммитить отдельным логическим изменением согласно правилам Agent Driver.
Нельзя включать его в release случайно только для получения чистого worktree.

Почему: remediation release должен иметь обозримый compatibility scope, а
PentestLens не должен принимать дополнительное provider behavior без
отдельного changelog/test evidence.

## Явные non-goals

Для закрытия этого запроса не требуется:

- переносить в Agent Driver tenant, Engagement, scope, pentest risk, autonomy,
  target budgets или сетевую policy PentestLens;
- делать Agent Gateway durable — Option 2 с явным readiness rejection остаётся
  допустимой;
- переписывать все внутренние модули ради нового layout;
- реализовывать UI, лаборатории или pentest-инструменты;
- изменять PentestLens repository из upstream Goal;
- публиковать пакет в публичный package index, если exact GitHub SHA + wheel
  handoff достаточны для downstream pinning.

## Обязательная итоговая проверка

В финальном handoff привести точные команды и результаты как минимум для:

1. полного unit test suite;
2. export snapshot и embedded e2e example только на supported imports;
3. ToolGate provenance lifecycle/adversarial matrix из R1;
4. two-client real-Postgres approval race, replay/conflict/stale/crash matrix
   из R2;
5. plan binding checkpoint/resume/trace/overwrite tests из R3;
6. Stop/cancellation/restart/late-result matrix из R4;
7. Gateway non-durable readiness rejection;
8. lint, type, docs и supported Python matrix;
9. двух изолированных wheel builds и проверки SHA-256/METADATA/imports;
10. `git status`, public remote URL, release commit SHA и handoff commit SHA.

Тесты не должны обращаться к PentestLens, реальной pentest-цели или требовать
секретов OpenRouter.

## Терминальное условие upstream Goal

Goal завершён только когда одновременно истинно следующее:

- R0–R6 выполнены без ослабления утверждённого контракта;
- обязательные U2/U3/U4/U5 пути доказаны сквозными и durable тестами;
- все required изменения находятся в одном новом release source SHA;
- wheel воспроизводим и соответствует этому SHA;
- handoff содержит проверяемые identities и полные acceptance receipts;
- GitHub branch и локальный release checkout чисты;
- не осталось required TODO в handoff, epic status, notes, skipped tests или
  unreleased commits.

После этого PentestLens независимо проверит handoff по неизменённой копии
`upstream-requirements.md` и только затем переведёт EPIC-03A в `in_progress`.

## Формат ответа для передачи в PentestLens

Вернуть одним сообщением:

```text
Agent Driver release version:
Release source commit SHA:
Handoff document commit SHA/path:
Wheel filename:
Wheel SHA-256:
Approved requirements SHA-256:
Public remote:
Clean worktree result:
Full tests result:
Lint/type/docs/Python-matrix result:
R1 provenance matrix result:
R2 Postgres approval matrix result:
R3 plan-binding trace result:
R4 Stop/cancellation matrix result:
Residual risks (none may be a required item):
```

