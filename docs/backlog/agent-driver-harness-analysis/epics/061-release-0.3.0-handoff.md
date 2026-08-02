# R5 — Единый согласованный релиз 0.3.0 + reproducible wheel + handoff

Статус: **pending** (blocked by 057–060 + 056a). Заменяет U7/эпик 055 в части релиза.
Контекст: `../REMEDIATION_PLAN.md` §061.

## Решение по версии (подтверждено)

**`0.3.0`** — после `0.2.0` добавлены новые публичные символы (`agent_driver.embedding`) ⇒ minor-bump.
`0.2.0` identity/wheel/hash **не переиспользуются**.

## Предусловие

057–060 + 056a закрыты; весь required-код в дереве; worktree чист; 402-патч уже отдельным коммитом
(`fix/openrouter-credit-402` `5883268`), **не** в release identity.

## Фазы

- A. **Версия** — bump до 0.3.0 по release-policy.
- B. **Синхронизация identity** — package version == `agent_driver.__version__` == wheel METADATA ==
  changelog == docs (guard `test_version.py` + export-snapshot).
- C. **Post-cut namespace** — включить `agent_driver.embedding` + exact export-snapshot.
- D. **Reproducible wheel** — две изолированные сборки с фикс. `SOURCE_DATE_EPOCH` → идентичный SHA-256.
- E. **Handoff-receipts** — exact filename/size/SHA-256/`SOURCE_DATE_EPOCH`/Python/builder + команды
  проверки imports/METADATA; различить release-source-SHA и (если есть) doc-commit; результаты всех 10
  пунктов «Обязательной итоговой проверки» запроса, включая **фактически выполненную real-Postgres matrix**.

## Acceptance (1:1 с R5)

Идентичности совпадают; exact release-commit содержит весь код+тесты; repro-wheel идентичен; handoff с
полными receipts; unit-suite + adversarial + lint/type/docs + Python-matrix + **обязательный postgres-job**
зелёные; `git status --porcelain` пуст; public GitHub commit доступен; нет required-кода только в
notes/patch/следующем unreleased-коммите.

## Не в скоупе

Публикация в public package index (exact GitHub SHA + wheel handoff достаточно для pinning).
