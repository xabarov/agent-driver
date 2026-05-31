# Deep Research And Skills Analysis

Status: active reference for Unified Work Plan Phases 4-5. Use
[Unified Work Plan](unified-work-plan-2026-05-31.md) for sequencing.

Дата: 2026-05-31.

Цель: понять, как лучшие практики Deep Research и Agent Skills из OpenAI,
Anthropic, Hermes и OpenClaude можно встроить в `agent-driver` без превращения
runtime в тяжелый DAG. Главная мысль: **Skills не заменяют Deep Research**.
Skills дают агенту процедурное знание и локальные инструменты "точно вовремя",
а Deep Research должен оставаться проверяемым runtime contract: план, поиск,
чтение источников, верификация, синтез, цитаты.

## Source Map

Внешние источники, проверенные 2026-05-31:

- OpenAI:
  [Introducing deep research](https://openai.com/index/introducing-deep-research/),
  [Deep research API](https://developers.openai.com/api/docs/guides/deep-research),
  [Web search](https://developers.openai.com/api/docs/guides/tools-web-search),
  [Migrate to Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses),
  [Agents SDK](https://developers.openai.com/api/docs/guides/agents),
  [Skills in ChatGPT](https://help.openai.com/en/articles/20001066-skills-in-chatgpt),
  [Using skills](https://openai.com/academy/skills/).
- Anthropic:
  [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system),
  [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents),
  [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents),
  [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills),
  [Introducing Agent Skills](https://claude.com/blog/skills),
  [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents),
  [Building agents with the Claude Agent SDK](https://claude.com/blog/building-agents-with-the-claude-agent-sdk).
- Local Hermes:
  `/home/roman/pyprojects/ML/hermes-agent/tools/skills_tool.py`,
  `/home/roman/pyprojects/ML/hermes-agent/agent/skill_commands.py`,
  `/home/roman/pyprojects/ML/hermes-agent/agent/skill_utils.py`,
  `/home/roman/pyprojects/ML/hermes-agent/tools/delegate_tool.py`,
  `/home/roman/pyprojects/ML/hermes-agent/skills/research/*`,
  `/home/roman/pyprojects/ML/hermes-agent/skills/software-development/*`.
- Local OpenClaude:
  `/home/roman/pyprojects/ML/openclaude/src/skills/loadSkillsDir.ts`,
  `/home/roman/pyprojects/ML/openclaude/src/tools/SkillTool/SkillTool.ts`,
  `/home/roman/pyprojects/ML/openclaude/src/tools/SkillTool/prompt.ts`,
  `/home/roman/pyprojects/ML/openclaude/src/types/command.ts`,
  `/home/roman/pyprojects/ML/openclaude/src/tools/AgentTool/runAgent.ts`,
  `/home/roman/pyprojects/ML/openclaude/src/tools/WebSearchTool/*`.
- Current `agent-driver`:
  `agent_driver/runtime/research_session_contract.py`,
  `agent_driver/runtime/research_evidence.py`,
  `agent_driver/tools/builtin/web.py`,
  `agent_driver/observability/source_evidence.py`,
  `agent_driver/tools/builtin/skills.py`,
  `agent_driver/tools/builtin/agent.py`,
  `agent_driver/subagents/*`.

## Executive Summary

OpenAI и Anthropic сходятся на одной архитектурной форме для глубокого
исследования: агент сам строит search strategy, многократно ищет, открывает
источники, меняет план по новым находкам, затем синтезирует ответ с цитатами.
У Anthropic production Research явно multi-agent: lead researcher, параллельные
subagents, memory для плана и отдельный citation agent. У OpenAI Deep Research
доступен как product/API capability поверх Responses, web/file/MCP/code tools,
background mode и источников в output items.

Skills в этой картине отвечают за другую ось: они позволяют не класть все
процедуры в системный prompt. Агент видит только `name`/`description`, а полное
`SKILL.md`, references, scripts и assets читает только когда skill подходит
задаче. Хороший skill является маленьким onboarding packet: когда использовать,
какой процесс соблюдать, какие инструменты/скрипты есть, какие ошибки не
допускать. Для Deep Research это особенно ценно: разные research-домены требуют
разных источников, цитирования, эвристик свежести, API и форматов отчета.

`agent-driver` уже сильнее, чем кажется: есть research depth contract,
разделение `web_search` как candidates и `web_fetch` как verified evidence,
source shelf, provider capability work, bounded repair, subagents и trace
summary. Но Skills пока являются только discovery tool. Следующий качественный
скачок: сделать Skills активным контекстным механизмом, который работает через
тот же governed tool/runtime слой, а не через скрытую магию prompt injection.

Рекомендуемая форма: **Deep Research = research contract + source ledger +
subagent orchestration + optional skills**. Не наоборот.

## External Findings

### OpenAI Deep Research

OpenAI product post от 2025-02-02 описывает Deep Research как агентную
возможность: пользователь дает prompt, ChatGPT ищет, анализирует и синтезирует
много онлайн-источников в report-level ответ. Обновление 2026-02-10 важно для
нас: Deep Research может подключаться к MCP/apps, ограничивать web search
trusted sites, показывать progress и принимать interruption/refinement в ходе
исследования.

API docs дают практичный contract:

- Deep research запускается через Responses API.
- Нужно дать хотя бы один data source: web search, remote MCP или file search
  с vector stores; code interpreter может добавляться для анализа.
- Такие runs могут быть долгими, поэтому background mode и webhook/polling
  являются штатным способом интеграции.
- Output сохраняет не только final message, но и items: `web_search_call`,
  `file_search_call`, `mcp_tool_call`, `code_interpreter_call`; final answer
  содержит inline citations.

OpenAI web-search docs отдельно выделяют три уровня: fast lookup,
agentic search и deep research. Это полезная классификация для нашего
`research_depth`: не каждый web answer должен становиться дорогим research
run. Важный UI contract: если модель использовала web search, цитаты должны
быть явно видимыми и кликабельными; `sources` и annotations не должны
теряться в adapter layer.

Практический вывод для `agent-driver`: provider-native Deep Research можно
поддержать как future adapter path, но core runtime лучше строить
provider-neutral. Нам нужны единые `EvidenceRecord`, trace summary и citation
coverage независимо от того, пришли источники из OpenAI Responses, нашего
`web_fetch`, MCP или file search.

### Anthropic Deep Research

Anthropic Research production post дает наиболее полезную инженерную рамку:

- Lead agent анализирует задачу, пишет стратегию и создает subagents для
  независимых аспектов.
- Subagents работают как intelligent filters: сами ищут, оценивают результаты
  и возвращают compact findings.
- Lead agent решает, достаточно ли информации, или нужны новые subagents /
  уточнение стратегии.
- Citation agent отдельно проходит по документам и отчету, чтобы claims были
  привязаны к конкретным источникам.
- План сохраняется в memory, потому что большой контекст может быть
  усечен; это ровно наша зона compaction/session memory.

Отрезвляющая часть: Anthropic пишет, что multi-agent research резко дороже.
Обычные agents уже используют больше токенов, multi-agent может быть на порядок
дороже chat. Значит subagents должны включаться по ценности задачи:
parallelizable, source-heavy, context-heavy, many tools. Для маленького вопроса
один `web_search -> web_fetch -> final` дешевле и надежнее.

`Building effective agents` усиливает это правило: сначала простые composable
patterns, потом complexity. Для research особенно подходят
orchestrator-workers и evaluator-optimizer. Последний можно реализовать
без тяжелого workflow engine: после первичного синтеза отдельный verifier
проверяет "какие claims не покрыты sources, где слабая diversity, что
устарело".

### Skills

Anthropic Agent Skills и OpenAI Skills сходятся на формате:

- skill = директория с `SKILL.md`;
- frontmatter минимум `name` + `description`;
- full instructions загружаются только при activation;
- references/templates/scripts/assets читаются лениво;
- skill может включать deterministic code, когда программирование надежнее
  генерации токенов;
- security не optional: skills могут содержать инструкции и код, поэтому
  источник должен быть trusted/audited.

Anthropic особо подчеркивает progressive disclosure:

- tier 1: metadata в prompt/catalog;
- tier 2: body `SKILL.md` при activation;
- tier 3: supporting files only as needed.

OpenAI Help Center на 2026-05-31 говорит, что Skills в ChatGPT являются
reusable/shareable workflows с instructions, examples and code; поддерживаются
в ChatGPT Business/Enterprise/Edu/etc., Codex и API, и следуют Agent Skills
open standard. Для нас это важный interoperability signal: если мы добавляем
skills, лучше не изобретать несовместимый формат.

### Context Engineering

Anthropic context-engineering post хорошо формулирует то, что мы уже видим в
тестах: большой контекст не равен хорошему контексту. Агентный поиск лучше
держит lightweight identifiers: file paths, queries, URLs, resource IDs, а
затем подтягивает нужное через tools. Skills идеально вписываются сюда: skill
catalog в prompt, skill body/resources только когда они реально нужны.

Для Deep Research это означает: source ledger и skill refs должны переживать
compaction, но полные HTML/PDF/tool payloads не должны бездумно жить в main
context.

## Neighbor Project Findings

### Hermes

Hermes имеет зрелую Skill-систему, близкую к тому, что стоит построить у нас:

- `skills_list` возвращает только metadata: name, description, category.
- `skill_view` загружает full content или конкретный supporting file.
- `SKILL.md` поддерживает YAML frontmatter, category directories, external
  skill roots, platform gating, disabled lists, plugin skills.
- Есть prerequisites/setup: env vars, commands, secret capture, setup notes.
- Есть basic injection scan для suspicious patterns.
- Slash commands (`/skill-name`) строятся из skill metadata; skill invocation
  добавляет activation note, skill directory, config values and supporting
  file hints.
- Preloaded skills можно добавить в session prompt явно; reload skills не
  сбрасывает prompt cache, потому что skills вызываются runtime-механизмами.

Полезная деталь Hermes: skill message всегда сообщает absolute skill directory
и объясняет, что relative paths внутри skill надо резолвить от него. Это
простая вещь, но она резко уменьшает ошибки при scripts/templates.

Hermes research skills показывают правильный стиль content:

- `research-paper-writing` не просто "пиши статью", а задает lifecycle:
  setup, literature review, experiments, analysis, drafting, review,
  submission.
- `arxiv` содержит конкретные API commands, parsing examples, Semantic Scholar
  extension и citation discipline.

Hermes `delegate_task` тоже важен: child получает fresh conversation,
restricted toolset, свой task id, а parent видит summary result, не весь tool
noise. Для Deep Research это почти готовая модель worker isolation.

Главный риск Hermes-подхода для нас: он богаче и больше. В `agent-driver`
нельзя сразу тащить весь skill hub, plugins, secret capture и inline shell
preprocessing. Нужен минимальный slice.

### OpenClaude

OpenClaude показывает более productized SkillTool:

- Skills являются prompt commands в общем command registry.
- Frontmatter богаче: `allowed-tools`, `model`, `effort`, `context: fork`,
  `agent`, `paths`, `hooks`, `disable-model-invocation`, `user-invocable`,
  `when_to_use`.
- Skill listing получает budget: около 1% context window; descriptions
  truncation, bundled skills приоритетнее.
- Prompt прямо говорит: если skill matches, model must call Skill tool before
  answering. Это сильное правило, но у нас его стоит делать осторожно:
  explicit skill invocation hard, automatic match soft/repairable.
- `SkillTool` может выполнить skill inline или forked sub-agent. Forked skill
  получает отдельный context/token budget и возвращает result.
- `runAgent` умеет preload skills из agent frontmatter, то есть subagent может
  стартовать уже с нужными процедурными знаниями.
- `invokedSkills` сохраняются в state, чтобы переживать compaction и не терять
  факт активной инструкции.
- Permissions учитывают skill-level allow/deny; safe skills могут auto-allow,
  остальные спрашивают.

Для Deep Research OpenClaude также полезен `WebSearchTool`:

- provider chain в `auto` режиме fallback'ится;
- explicit provider mode fails loudly, без скрытого переключения;
- native Anthropic/Codex paths отделены от external search providers;
- prompt требует `Sources:` в финале.

Это хороший UX/engine contract для нас: если пользователь явно выбрал источник
или provider, отказ должен быть видимым; если выбрал auto, можно fallback.

## Current Agent-Driver Gap

Что уже хорошо:

- `research_depth`: `none | light_search | source_verified_report`.
- `ResearchSessionContract` проверяет search/fetch counts, source diversity,
  final source links и visible todos.
- `web_search` уже трактуется как candidate discovery, `web_fetch` как
  verification layer.
- `source_evidence_from_tool_result` нормализует web search/fetch evidence и
  дедуплицирует источники.
- `agent_tool` и `subagents/*` уже дают durable child runs, handoff, bounded
  artifact refs, worker tool surfaces, merge provenance.
- Prompt fragments уже разделены по available tools.

Главный gap:

`agent_driver/tools/builtin/skills.py` сейчас только ищет `SKILL.md` и
помечает trust roots. Он не парсит frontmatter, не возвращает descriptions,
не умеет загрузить skill body, не умеет invoke, не учитывает allowed tools,
не сохраняет invoked skill через compaction, не preload'ит skills в subagents.

Из-за этого мы теряем наиболее полезную связку:

`research task -> classify -> find relevant skill metadata -> load skill body -> use domain workflow -> run research contract`.

## Design Position

### 0. Shared Runtime First, Chat-Demo As UX

Deep Research and Skills must be implemented as reusable `agent_driver`
capabilities first. Chat-demo is allowed to expose them as product UX, but it
must not become a second research/skills engine.

Concrete boundary:

- `agent_driver` owns `research_depth`, `ResearchSessionContract`, source
  ledger, final-readiness checks, progress/runtime events, skill manifest
  parsing, trust policy, `skill_view`, invocation records, compaction survival,
  skill-aware subagent preload and SDK contracts.
- `examples/chat-demo` owns the button/segmented control for Deep Research,
  skill library UI, upload/install screens, trust warning presentation,
  progress cards, citation/source inspector, child-run panel and deterministic
  UI/live probes.
- The demo backend should translate UI choices into shared
  `AgentRunInput`/SDK/session calls. It should not parse `SKILL.md`, maintain a
  private evidence ledger, decide research final-readiness or implement
  demo-only skill invocation.
- If another frontend or SDK user would need the behavior, it belongs in
  `agent_driver`.

This is what keeps the OpenClaude/Hermes principle intact: the demo stays a
product integration gate, not the place where reusable runtime logic quietly
forks.

### 1. Skills Are Runtime Context, Not Hidden System Prompt

Не стоит автоматически вшивать все skills в base prompt. Нужно:

- в system/reminder давать compact skill catalog только из metadata;
- full skill грузить только через tool call или explicit preload;
- в trace фиксировать, какой skill был загружен, откуда, trusted ли он,
  сколько context добавил;
- при compaction сохранять skill invocation record, а не всю простыню body.

### 2. Deep Research Is A Contract

Skill может научить агента "как исследовать arXiv" или "как делать market
analysis", но runtime все равно должен проверять:

- были ли реальные source reads;
- достаточна ли diversity;
- есть ли final citations;
- не выданы ли search candidates как sources;
- не осталось ли незавершенных todos;
- не провалился ли provider до synthesis.

Это уже направление текущего `ResearchSessionContract`; его надо расширять, а
не заменять skill prompt'ами.

### 3. Multi-Agent Research Must Be Gated By Task Shape

Subagents стоит включать когда есть хотя бы один сигнал:

- пользователь явно просит deep/глубокое исследование, отчет, обзор, сравнение;
- задача разбивается на независимые вопросы;
- expected evidence не помещается в один context;
- нужно сравнить несколько доменов/поставщиков/версий;
- нужна отдельная verification/citation pass.

Для простых current facts multi-agent будет дорогой задержкой.

### 4. Provider-Native Deep Research Is An Optimization Path

OpenAI Responses Deep Research, OpenAI web search, Anthropic native web search,
MCP/file search должны попадать в один evidence ledger. Если provider умеет
native deep research, можно дать adapter path. Но основной runtime не должен
зависеть от одного provider API, потому что `agent-driver` уже живет с
OpenRouter/OpenAI-compatible/local providers.

## Proposed Architecture

### Skill Catalog

Добавить typed model:

```python
SkillManifest:
  name: str
  description: str
  when_to_use: str | None
  version: str | None
  source: "project" | "user" | "managed" | "external"
  path: Path
  skill_dir: Path
  trusted: bool
  allowed_tools: tuple[str, ...]
  context: "inline" | "fork"
  agent_profile: str | None
  tags: tuple[str, ...]
  paths: tuple[str, ...]
  metadata: dict
```

Discovery roots:

- project-local: `.agent-driver/skills/` or `skills/`;
- compatibility: `.codex/skills/`, `.claude/skills/` if configured;
- user/global roots from config/env;
- explicit trusted roots from tool args/policy.

Startup/prompt should load only `name`, `description`, `when_to_use`, trust and
maybe tags. Full body stays out of prompt until activation.

### Skill Tools

Replace or extend current `skill_tool` into two surfaces:

- `skill_list`: metadata only, optional query/category/trust filter;
- `skill_view` or `skill_invoke`: load full `SKILL.md`, optional supporting
  file, and return a normalized activation payload.

Activation payload should include:

- skill name/path/trust/source;
- body content after safe template substitution;
- skill directory;
- supporting file index only, not file contents;
- allowed tools and context mode;
- warnings: untrusted, hidden path, suspicious instruction, missing required
  env/config, unsupported platform.

For `context: fork`, runtime should convert skill invocation into an
`agent_tool`/subagent run with skill content in child initial context. Parent
gets result only.

### Skill Triggering

Three activation modes:

1. Explicit user invocation: `/skill`, "use skill X", or tool call with exact
   skill. This should be hard: load the skill or explain why not.
2. Model-chosen activation: model sees catalog and calls `skill_view` when a
   skill matches. This should be encouraged by prompt, not hidden.
3. Runtime suggestion: task classifier detects `source_verified_report` and
   adds a reminder such as "Relevant skills available: deep-research-report,
   source-triangulation; call skill_view before research if useful." This is
   softer than auto-loading and keeps agency/trace visible.

OpenClaude's "BLOCKING REQUIREMENT" is useful for explicit slash commands; for
semantic auto-match it can be too aggressive and cause false-positive skill
calls before simple answers.

### Research Skills

Start with a small curated skill set:

- `deep-research-report`: plan, search, fetch, synthesize, cite, gaps.
- `source-triangulation`: prefer primary/official sources, check dates,
  compare at least two domains, flag conflicts.
- `provider-doc-research`: official docs only for provider/API/library
  questions, version/date awareness.
- `literature-review`: arXiv/Semantic Scholar/OpenReview workflow, citation
  sanity, no hallucinated papers.
- `market-competitive-analysis`: company/product comparison, pricing/date
  freshness, table output.
- `citation-auditor`: claim-to-source coverage, broken citation detection.

These skills should not bypass `ResearchSessionContract`. They should teach
the model how to satisfy it.

### Source Ledger

Unify source tracking beyond web:

```python
EvidenceRecord:
  source_id: str
  kind: "search_candidate" | "fetched_page" | "file_chunk" | "mcp_resource" | "assistant_link"
  url: str | None
  resource_uri: str | None
  title: str | None
  domain: str | None
  published_at: str | None
  retrieved_at: str
  tool_name: str
  tool_call_id: str
  status: "candidate" | "verified" | "failed" | "blocked"
  excerpt: str | None
  claim_refs: tuple[str, ...]
```

Search candidates stay candidates. Verified evidence starts only at successful
fetch/file/MCP read or explicit final links with enough support. UI should keep
the current distinction: `Search candidates` vs `Sources`.

### Chat-Demo UX Contract

When runtime contracts exist, chat-demo should expose:

- Deep Research mode selector/button that maps to shared `research_depth` and
  optional strategy hints;
- progress surface for search, fetch, file/MCP/code calls, subagent handoffs,
  context-pressure recommendations and synthesis;
- source shelf that renders `EvidenceRecord` status:
  candidates, verified sources, failed/blocked reads and assistant links;
- citation inspector that shows claim/source coverage from the shared ledger;
- skill library UI with install/upload/review flows, trust badges, allowed-tool
  hints and supporting-file preview;
- skill invocation timeline sourced from runtime events/metadata;
- child-run panel for research workers and citation/verifier workers.

The demo must render shared events/contracts. It should not infer state from
assistant prose or maintain a parallel research checklist.

### Research Orchestration

Keep the single-agent loop as default. Add a small strategy layer:

- `light_search`: one search/fetch pass, no subagents.
- `source_verified_report`: at least search + fetch + synthesis + citations.
- `deep_parallel_research`: optional when task is parallelizable or explicitly
  deep. Lead agent creates bounded child tasks.

Child task template:

- self-contained question;
- allowed sources/tools;
- expected output schema: findings, sources, confidence, gaps;
- max sources/fetches;
- no final answer to user; return compact evidence summary.

Lead agent owns final synthesis. A separate verifier/citation child may run
after synthesis when stakes are high.

### Context And Compaction

Persist:

- skill invocation records: name, path, trusted, digest, loaded_at, agent_id;
- source ledger compact records;
- research plan and open gaps;
- child summaries and source refs.

Do not persist full skill body or full fetched content into session memory
unless explicitly requested. On resume/compaction, rehydrate by skill path/digest
if still available; otherwise warn that the skill changed or disappeared.

## Implementation Phases

### Phase 1 - Skill Metadata Foundation

- [ ] Add `agent_driver.skills` package with frontmatter parser and
  `SkillManifest`.
- [ ] Extend current `skill_tool` to parse `name`, `description`, optional
  `when_to_use`, `allowed-tools`, `context`, `agent`, `tags`, `version`.
- [ ] Support project/user/trusted roots and path exclusions.
- [ ] Add deterministic tests for malformed frontmatter, hidden dirs,
  symlink/duplicate handling, trust classification and description caps.

Acceptance: `skill_tool` returns useful metadata, not just file paths.
Chat-demo, CLI and SDK can all consume the same skill metadata shape.

### Phase 2 - Skill View / Invocation

- [ ] Add `skill_view` behavior: load full body or one supporting file.
- [ ] Return skill directory and supporting file index.
- [ ] Add safety warnings for untrusted roots and suspicious instructions.
- [ ] Record `SkillInvocation` in run metadata/events.
- [ ] Add prompt fragment: "use skill_view when relevant; do not mention skills
  without loading them."

Acceptance: a model can discover a skill, load it, follow it, and trace summary
shows the invocation.

### Phase 3 - Research Skill Pack

- [ ] Add curated project skills under `skills/research/` or
  `.agent-driver/skills/research/`.
- [ ] Wire task classifier to suggest relevant research skills for
  `source_verified_report`.
- [ ] Add tests that skill metadata appears only when skill tools are allowed.
- [ ] Add one deterministic fake-provider scenario where a research skill
  causes the correct `web_search -> web_fetch -> cited final` loop.

Acceptance: skills improve research behavior without weakening evidence gates.

### Phase 4 - Skill-Aware Subagents

- [ ] Allow `agent_tool` metadata to include `preload_skills`.
- [ ] Child handoff loads skill bodies into child context when trusted/allowed.
- [ ] Add worker profiles: `researcher`, `citation_verifier`, `source_auditor`.
- [ ] Child outputs must include compact findings + source refs, not raw
  transcript.

Acceptance: parallel research can preload relevant skills and parent still owns
final synthesis.

### Phase 5 - Evidence Ledger V2

- [ ] Promote current source evidence helpers into a first-class ledger.
- [ ] Track search candidates, verified reads, failed/blocked reads, file/MCP
  resources.
- [ ] Add claim-to-source coverage for final answers where possible.
- [ ] Expose ledger in trace summary and chat-demo source shelf.

Acceptance: UI and trace can explain why a research final was allowed,
repaired, partial or blocked.

### Phase 5A - Product UX Adapter

- [ ] Add chat-demo Deep Research mode UX over shared runtime contracts.
- [ ] Add chat-demo Skills library/installation UX over `agent_driver.skills`.
- [ ] Add backend endpoints only as thin adapters to SDK/runtime contracts.
- [ ] Add deterministic fake scenarios for Deep Research progress, skill load,
  untrusted skill review, citation coverage and provider failure after search.

Acceptance: chat-demo proves UX and traceability without owning reusable
research or skill logic.

### Phase 6 - Eval Gate

Add eval/live scenarios:

- simple current lookup: no skill, no subagent;
- deep report: skill suggested/loaded, >=2 fetched sources, citations;
- literature review: arXiv/Semantic Scholar workflow, no fake citations;
- provider docs: official sources only;
- parallel comparison: two researcher children + synthesis;
- malicious/untrusted skill: warning/permission behavior;
- compaction after skill invocation: skill record survives, full body not
  duplicated;
- provider failure after search: no fake `Sources`.

Acceptance: improvements are measured by trace verdicts, not vibes.

## Risks And Guardrails

- **Prompt injection via skills.** Trusted roots, warnings, permission checks,
  no automatic script execution from untrusted skills.
- **Skill bloat.** Catalog budget, description caps, full body only on demand.
- **False-positive skill invocation.** Explicit slash commands are hard; semantic
  match is suggested, not hidden auto-load.
- **Source laundering.** A skill can recommend sources, but only ledger-verified
  reads count as evidence.
- **Subagent token burn.** Multi-agent mode only for high-value, parallelizable
  tasks; trace token/cost accounting required.
- **Provider mismatch.** Native OpenAI/Anthropic search/deep-research outputs
  must normalize into the same ledger as local web tools.
- **Compaction drift.** Store skill digest and path; warn if skill content
  changed before resume.

## My Recommendation

Do not start by building "Deep Research DAG". Start by making Skills real and
keeping Deep Research contract-driven.

The highest-leverage first slice is:

1. Upgrade `skill_tool` into metadata + `skill_view`.
2. Add a small curated `deep-research-report` skill.
3. Have `source_verified_report` prompt suggest that skill when available.
4. Preserve existing `ResearchSessionContract` as the final-readiness gate.
5. Add one trace-backed eval proving the skill improves source-backed final
   answers without allowing search-only finals.

This keeps the architecture sympathetic to the current codebase: small
contracts, governed tools, prompt fragments, trace gates. It also aligns with
both OpenAI and Anthropic: agents get tools and just-in-time context, but the
system still owns observability, permissions and source truth.
