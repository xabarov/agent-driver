"""Live CLI evaluation harness and trace analytics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import time
from typing import Any

from agent_driver.cli.providers import CliProviderConfig, build_cli_provider
from agent_driver.cli.tools import CliToolConfig, build_cli_toolset
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.tools import ToolSet
from agent_driver.runtime import RuntimeStoreFactoryConfig, create_runtime_store_bundle
from agent_driver.sdk import create_agent

_LIVE_OPT_IN_ENV = "AGENT_DRIVER_RUN_LIVE_CLI_EVALS"
_REDACT_KEYS = {"api_key", "authorization", "token", "password", "secret", "bearer"}


class LiveEvalSkipped(RuntimeError):
    """Raised when live eval should be skipped with explanation."""


@dataclass(frozen=True, slots=True)
class EvalScenario:
    """One CLI live evaluation scenario."""

    scenario_id: str
    prompt: str
    expected_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    expected_answer_contains: tuple[str, ...] = ()
    expected_answer_any_of: tuple[tuple[str, ...], ...] = ()
    follow_up_prompts: tuple[str, ...] = ()
    max_steps: int = 12
    max_tool_calls: int = 6
    deadline_seconds: float = 120.0
    tags: tuple[str, ...] = ()
    expected_min_tool_calls: int = 0
    expected_tool_chain_contains: tuple[str, ...] = ()
    sandbox_required: bool = False
    tool_packs: tuple[str, ...] = ()
    allow_dangerous_tools: bool = False
    prompt_template: str | None = None
    required_tools: tuple[str, ...] = ()


def default_live_scenarios() -> list[EvalScenario]:
    """Return fixed 10-scenario live CLI evaluation suite."""
    return [
        EvalScenario(
            scenario_id="news_web_search",
            prompt="Какие сегодня ключевые новости в Греции? Кратко и по пунктам.",
            expected_tools=("web_search",),
            tags=("web_search", "news"),
        ),
        EvalScenario(
            scenario_id="url_summary",
            prompt="Открой https://example.com и кратко перескажи содержание страницы.",
            expected_tools=("web_fetch",),
            tags=("web_fetch",),
        ),
        EvalScenario(
            scenario_id="repo_lookup",
            prompt="Найди где в этом репозитории реализован command 'chat' и коротко опиши.",
            expected_tools=("glob_search", "grep_search"),
            tags=("filesystem_read",),
        ),
        EvalScenario(
            scenario_id="code_explanation",
            prompt="Объясни что делает функция build_cli_provider и какие у нее источники конфигурации.",
            expected_tools=("glob_search",),
            expected_answer_contains=("provider",),
            tags=("filesystem_read", "reasoning"),
        ),
        EvalScenario(
            scenario_id="multi_step_research",
            prompt="Собери 3 источника про последние новости AI и сделай сжатый дайджест.",
            expected_tools=("web_search", "web_fetch"),
            tags=("multi_step", "synthesis"),
        ),
        EvalScenario(
            scenario_id="zero_result_behavior",
            prompt="Найди новости по очень редкому запросу zxqvzzqv news и честно скажи если ничего нет.",
            expected_tools=("web_search",),
            tags=("zero_result",),
        ),
        EvalScenario(
            scenario_id="ambiguous_request",
            prompt="Сделай как надо, но аккуратно и без ошибок.",
            expected_tools=(),
            tags=("ambiguous", "clarification"),
        ),
        EvalScenario(
            scenario_id="planning_state",
            prompt="Составь план из 4 шагов по проверке CLI и обнови planning state.",
            expected_tools=("todo_write",),
            tags=("planning",),
        ),
        EvalScenario(
            scenario_id="no_tool_reasoning",
            prompt="Объясни разницу между latency и throughput в двух абзацах.",
            forbidden_tools=("web_search", "web_fetch"),
            tags=("reasoning_only",),
        ),
        EvalScenario(
            scenario_id="dangerous_tool_request",
            prompt="Выполни shell-команду ls -la и покажи результат.",
            forbidden_tools=("bash", "powershell_tool"),
            tags=("dangerous_tool",),
        ),
    ]


def default_deep_scenarios() -> list[EvalScenario]:
    """Return deep suite focused on current and new risky paths."""
    return [
        EvalScenario(
            scenario_id="sandbox_build_verify",
            prompt=(
                "В каталоге sandbox создай greet.py с greet(name: str) -> str "
                "и верни строку 'Hello, <name>!'. Затем добавь test_greet.py "
                "с unittest.TestCase, проверь через python -m unittest test_greet -v, "
                "исправь баги если есть, и покажи финальный результат."
            ),
            prompt_template=(
                "Работай в текущей рабочей директории (это sandbox). "
                "Сначала вызови todo_write ровно один раз с валидной схемой "
                "(todos=[{{id,content,status}}], не более одного in_progress). "
                "После первого todo_write больше не вызывай todo_write. "
                "Создай greet.py и test_greet.py через file_write с относительными путями. "
                "Если нужно исправление — только file_edit. "
                "Bash используй только для запуска python-команд и передавай cwd={sandbox}. "
                "Никогда не используй mkdir/cd/redirection/tee в bash. "
                "Если bash вернул denied — сразу исправь синтаксис и не повторяй тот же шаблон. "
                "Никогда не используй ';', '&&', '||' или '|' в bash-команде. "
                "Сделай один вызов bash: python -m unittest test_greet -v. "
                "Если тест упал, исправь через file_edit и снова запусти тот же bash без добавления других shell-команд. "
                "После успешного unittest обязательно вызови read_file для greet.py и test_greet.py. "
                "Запрещено давать финальный ответ до двух вызовов read_file."
            ),
            expected_tools=("todo_write", "file_write", "bash", "read_file"),
            expected_answer_contains=("Hello", "OK"),
            max_tool_calls=12,
            max_steps=18,
            deadline_seconds=300.0,
            tags=("deep", "sandbox", "filesystem_write", "shell"),
            expected_min_tool_calls=5,
            expected_tool_chain_contains=("file_write", "bash", "read_file"),
            required_tools=("todo_write", "file_write", "bash", "read_file"),
            sandbox_required=True,
            tool_packs=("planning", "filesystem_read", "filesystem_write", "shell"),
            allow_dangerous_tools=True,
        ),
        EvalScenario(
            scenario_id="file_edit_minimal_patch",
            prompt=(
                "В sandbox создай module.py и затем точечно исправь greeting через file_edit. "
                "Покажи итоговое содержимое файла."
            ),
            prompt_template=(
                "Работай в текущей рабочей директории (это sandbox). "
                "Сначала вызови todo_write ровно один раз с валидной схемой "
                "(todos=[{{id,content,status}}], не более одного in_progress). "
                "После первого todo_write больше не вызывай todo_write. "
                "Через file_write создай module.py с функцией: "
                "def greet(name): return f\"Hi, {{name}}!\". "
                "Затем одним file_edit замени 'Hi,' на 'Hello,'. "
                "Обязательно вызови read_file для module.py и только после этого дай финальный ответ. "
                "Запрещено давать финальный ответ до read_file."
            ),
            expected_tools=("todo_write", "file_write", "file_edit", "read_file"),
            forbidden_tools=("bash",),
            expected_answer_contains=("Hello",),
            max_tool_calls=6,
            max_steps=14,
            deadline_seconds=180.0,
            tags=("deep", "sandbox", "filesystem_write", "file_edit"),
            expected_min_tool_calls=4,
            expected_tool_chain_contains=("file_write", "file_edit", "read_file"),
            required_tools=("file_write", "file_edit", "read_file"),
            sandbox_required=True,
            tool_packs=("planning", "filesystem_read", "filesystem_write"),
            allow_dangerous_tools=True,
        ),
        EvalScenario(
            scenario_id="bash_denial_recovery",
            prompt=(
                "Проверь восстановление после denied в bash: выполни pwd и ls отдельными безопасными вызовами."
            ),
            prompt_template=(
                "Работай в текущей рабочей директории (sandbox). "
                "Сначала один раз вызови todo_write с валидной схемой. "
                "Затем выполни два отдельных вызова bash с cwd={sandbox}: "
                "1) pwd; 2) ls. "
                "Никогда не используй ';', '&&', '||', '|', redirection или tee. "
                "Если bash denied — исправь синтаксис и не повторяй тот же denied вызов. "
                "В ответе укажи результаты pwd и ls."
            ),
            expected_tools=("todo_write", "bash"),
            expected_answer_contains=("pwd", "ls"),
            max_tool_calls=8,
            max_steps=14,
            deadline_seconds=180.0,
            tags=("deep", "shell", "denial_recovery"),
            expected_min_tool_calls=3,
            expected_tool_chain_contains=("bash", "bash"),
            required_tools=("bash",),
            sandbox_required=True,
            tool_packs=("planning", "filesystem_read", "shell"),
            allow_dangerous_tools=True,
        ),
        EvalScenario(
            scenario_id="loop_detection_force_final",
            prompt=(
                "Найди токен ZZZZZ_NEVER_FOUND_TOKEN_999 в agent_driver/cli/main.py и корректно заверши, если его нет."
            ),
            prompt_template=(
                "Сначала вызови todo_write с валидной схемой. "
                "Затем сделай один grep_search по токену ZZZZZ_NEVER_FOUND_TOKEN_999 в файле "
                "agent_driver/cli/main.py. "
                "Если совпадений нет, честно сообщи об этом и заверши без повторных поисков."
            ),
            expected_tools=("todo_write", "grep_search"),
            expected_answer_any_of=(
                ("not found", "не найден", "не найдено", "no match"),
            ),
            max_tool_calls=6,
            max_steps=12,
            deadline_seconds=150.0,
            tags=("deep", "loop_detection", "filesystem_read"),
            expected_min_tool_calls=2,
            expected_tool_chain_contains=("grep_search",),
            required_tools=("grep_search",),
            tool_packs=("planning", "filesystem_read"),
        ),
        EvalScenario(
            scenario_id="workspace_cwd_relative_paths",
            prompt=(
                "Проверь относительные пути в sandbox: запиши notes.txt и затем прочитай его."
            ),
            prompt_template=(
                "Работай в sandbox. "
                "Сначала todo_write с валидной схемой. "
                "Через file_write создай notes.txt (относительный путь) с текстом 'hello workspace'. "
                "Потом прочитай notes.txt через read_file (только относительный путь). "
                "В финальном ответе повтори содержимое."
            ),
            expected_tools=("todo_write", "file_write", "read_file"),
            forbidden_tools=("bash",),
            expected_answer_contains=("hello workspace",),
            max_tool_calls=6,
            max_steps=12,
            deadline_seconds=150.0,
            tags=("deep", "workspace_cwd", "filesystem_write", "filesystem_read"),
            expected_min_tool_calls=3,
            expected_tool_chain_contains=("file_write", "read_file"),
            required_tools=("file_write", "read_file"),
            sandbox_required=True,
            tool_packs=("planning", "filesystem_read", "filesystem_write"),
            allow_dangerous_tools=True,
        ),
        EvalScenario(
            scenario_id="web_zero_results_honest_finalize",
            prompt=(
                "Проверь поведение на пустом web_search: редкий запрос и честное завершение."
            ),
            prompt_template=(
                "Сначала вызови todo_write с валидной схемой. "
                "Сделай web_search по запросу 'zxqvzzqv news 9c0d'. "
                "Если результатов нет, явно сообщи 'no results' и заверши без повторных похожих поисков."
            ),
            expected_tools=("todo_write", "web_search"),
            forbidden_tools=("bash",),
            expected_answer_any_of=(
                ("no results", "нет результат", "ничего не найден"),
            ),
            max_tool_calls=6,
            max_steps=12,
            deadline_seconds=150.0,
            tags=("deep", "web", "zero_result"),
            expected_min_tool_calls=2,
            expected_tool_chain_contains=("web_search",),
            required_tools=("web_search",),
            tool_packs=("planning", "web"),
        ),
        EvalScenario(
            scenario_id="todo_status_lifecycle",
            prompt=(
                "Проверь lifecycle статусов todo_write с одним in_progress и переходом на следующий шаг."
            ),
            prompt_template=(
                "Вызови todo_write с 4 задачами, где есть статусы pending, in_progress, completed, cancelled "
                "и только один in_progress. "
                "Сделай grep_search по 'def _run_command' в agent_driver/cli/main.py. "
                "Потом обнови todo_write: предыдущий in_progress -> completed, следующий pending -> in_progress."
            ),
            expected_tools=("todo_write", "grep_search"),
            expected_answer_contains=("completed", "in_progress"),
            max_tool_calls=8,
            max_steps=14,
            deadline_seconds=180.0,
            tags=("deep", "planning", "todo_schema"),
            expected_min_tool_calls=3,
            expected_tool_chain_contains=("todo_write", "grep_search", "todo_write"),
            required_tools=("todo_write", "grep_search"),
            tool_packs=("planning", "filesystem_read"),
        ),
        EvalScenario(
            scenario_id="multi_file_rename",
            prompt=(
                "Сделай связанный рефакторинг по двум файлам: greet -> welcome."
            ),
            prompt_template=(
                "Работай в sandbox. "
                "Сначала todo_write с валидной схемой и больше не вызывай todo_write. "
                "Создай lib.py и main.py через file_write (относительные пути), где используется функция greet. "
                "Затем двумя отдельными file_edit переименуй greet в welcome в обоих файлах. "
                "После этого обязательно сделай два вызова read_file: сначала lib.py, потом main.py. "
                "Финальный ответ разрешен только после двух read_file."
            ),
            expected_tools=("todo_write", "file_write", "file_edit", "read_file"),
            forbidden_tools=("bash",),
            expected_answer_contains=("welcome",),
            max_tool_calls=8,
            max_steps=14,
            deadline_seconds=180.0,
            tags=("deep", "filesystem_write", "file_edit", "refactor"),
            expected_min_tool_calls=6,
            expected_tool_chain_contains=(
                "file_write",
                "file_write",
                "file_edit",
                "file_edit",
                "read_file",
                "read_file",
            ),
            required_tools=("file_write", "file_edit", "read_file"),
            sandbox_required=True,
            tool_packs=("planning", "filesystem_read", "filesystem_write"),
            allow_dangerous_tools=True,
        ),
        EvalScenario(
            scenario_id="python_sandbox_arithmetic",
            prompt=(
                "Вычисли сумму чисел 1..50 с помощью python tool и дай число и формулу."
            ),
            prompt_template=(
                "Сначала todo_write с валидной схемой. "
                "Используй только tool `python` для вычисления суммы 1..50. "
                "Не используй bash. "
                "В ответе укажи 1275 и формулу n*(n+1)/2."
            ),
            expected_tools=("todo_write", "python"),
            forbidden_tools=("bash",),
            expected_answer_contains=("1275",),
            max_tool_calls=6,
            max_steps=12,
            deadline_seconds=150.0,
            tags=("deep", "python_tool"),
            expected_min_tool_calls=2,
            expected_tool_chain_contains=("python",),
            required_tools=("python",),
            tool_packs=("planning", "python_exec"),
            allow_dangerous_tools=True,
        ),
        EvalScenario(
            scenario_id="forbidden_bash_governance",
            prompt=(
                "Покажи Python-файлы в agent_driver/cli, но bash запрещен."
            ),
            prompt_template=(
                "Пользователь просит вывод в стиле `ls -la`, но инструмент bash запрещен для этого сценария. "
                "Сначала todo_write с валидной схемой. "
                "Найди Python-файлы в agent_driver/cli через glob_search (без bash). "
                "Дай краткий список путей."
            ),
            expected_tools=("todo_write", "glob_search"),
            forbidden_tools=("bash",),
            expected_answer_contains=("agent_driver/cli",),
            max_tool_calls=6,
            max_steps=12,
            deadline_seconds=150.0,
            tags=("deep", "tool_governance", "filesystem_read"),
            expected_min_tool_calls=2,
            expected_tool_chain_contains=("glob_search",),
            required_tools=("glob_search",),
            tool_packs=("planning", "filesystem_read", "shell"),
            allow_dangerous_tools=True,
        ),
        EvalScenario(
            scenario_id="multi_file_summary_digest",
            prompt=(
                "Прочитай три контракта и дай структурный digest по каждому."
            ),
            prompt_template=(
                "Сначала todo_write с валидной схемой. "
                "Прочитай через read_file файлы: "
                "agent_driver/contracts/__init__.py, "
                "agent_driver/contracts/base.py, "
                "agent_driver/contracts/messages.py. "
                "Сделай краткий digest по каждому файлу (по 3 пункта)."
            ),
            expected_tools=("todo_write", "read_file"),
            expected_answer_contains=("contracts/__init__.py", "contracts/base.py", "contracts/messages.py"),
            max_tool_calls=8,
            max_steps=14,
            deadline_seconds=180.0,
            tags=("deep", "filesystem_read", "long_context"),
            expected_min_tool_calls=4,
            expected_tool_chain_contains=("read_file", "read_file", "read_file"),
            required_tools=("read_file",),
            tool_packs=("planning", "filesystem_read"),
        ),
        EvalScenario(
            scenario_id="chat_multi_turn_followup",
            prompt=(
                "Найди в репозитории файл agent_driver/cli/main.py и назови его путь."
            ),
            prompt_template=(
                "Сначала todo_write с валидной схемой. "
                "Найди agent_driver/cli/main.py через glob_search или grep_search. "
                "В ответе укажи путь agent_driver/cli/main.py."
            ),
            follow_up_prompts=(
                "Теперь опиши содержимое этого файла в 2-3 предложениях. "
                "Не повторяй полный путь в начале ответа.",
            ),
            expected_tools=("todo_write", "glob_search"),
            expected_answer_any_of=(
                ("main.py", "agent_driver/cli/main.py"),
                ("def", "command", "cli"),
            ),
            max_tool_calls=8,
            max_steps=16,
            deadline_seconds=240.0,
            tags=("deep", "real", "multi_turn"),
            expected_min_tool_calls=2,
            required_tools=("glob_search",),
            tool_packs=("planning", "filesystem_read"),
        ),
        EvalScenario(
            scenario_id="ambiguous_request_clarify_then_act",
            prompt="Сделай как надо, но аккуратно и без ошибок.",
            prompt_template=(
                "Запрос неоднозначен. Сначала задай один уточняющий вопрос пользователю "
                "(какой файл или задачу имеешь в виду). "
                "После уточнения найди agent_driver/cli/main.py через glob_search "
                "и кратко опиши, что в файле."
            ),
            follow_up_prompts=(
                "Имел в виду agent_driver/cli/main.py — найди и кратко опиши.",
            ),
            expected_tools=("glob_search",),
            expected_answer_any_of=(
                ("уточн", "clarif", "какой", "which"),
                ("main.py", "agent_driver/cli/main.py", "def", "cli"),
            ),
            max_tool_calls=8,
            max_steps=16,
            deadline_seconds=240.0,
            tags=("deep", "real", "ambiguous"),
            expected_min_tool_calls=1,
            required_tools=("glob_search",),
            tool_packs=("filesystem_read",),
        ),
        EvalScenario(
            scenario_id="real_refactor_small_module",
            prompt=(
                "В sandbox добавь docstring к функции extract_text_form_tool_calls "
                "в файле tool_call_parser.py."
            ),
            prompt_template=(
                "Работай в sandbox (текущая рабочая директория). "
                "Сначала todo_write с валидной схемой. "
                "Скопируй или создай tool_call_parser.py с функцией extract_text_form_tool_calls "
                "(можно упростить тело до pass). "
                "Через read_file прочитай файл, затем file_edit добавь docstring "
                "'Parse fallback tool-call blocks from plain assistant text.' перед функцией. "
                "Снова read_file и в финальном ответе подтверди наличие docstring (тройные кавычки)."
            ),
            expected_tools=("todo_write", "file_write", "read_file", "file_edit"),
            forbidden_tools=("bash",),
            expected_answer_contains=('"""',),
            max_tool_calls=8,
            max_steps=14,
            deadline_seconds=180.0,
            tags=("deep", "real", "refactor", "filesystem_write", "file_edit"),
            expected_min_tool_calls=5,
            expected_tool_chain_contains=("read_file", "file_edit", "read_file"),
            required_tools=("read_file", "file_edit"),
            sandbox_required=True,
            tool_packs=("planning", "filesystem_read", "filesystem_write"),
            allow_dangerous_tools=True,
        ),
    ]


def default_regression_scenarios() -> list[EvalScenario]:
    """Return stable scenarios kept for occasional regression sweeps."""
    return [
        EvalScenario(
            scenario_id="qwen_text_form_tool_call",
            prompt=(
                "Найди README.md в репозитории и назови путь. "
                "Используй инструменты аккуратно и заверши ответом."
            ),
            expected_tools=("glob_search",),
            expected_answer_contains=("README",),
            tags=("regression", "tool_call_fallback"),
            expected_min_tool_calls=1,
            required_tools=("glob_search",),
            tool_packs=("filesystem_read",),
        ),
        EvalScenario(
            scenario_id="glob_root_listing",
            prompt=(
                "Покажи только верхнеуровневые markdown файлы в текущей директории "
                "без рекурсивного обхода."
            ),
            expected_tools=("glob_search",),
            expected_answer_contains=("md",),
            forbidden_tools=("bash",),
            tags=("regression", "glob_semantics"),
            expected_min_tool_calls=1,
            required_tools=("glob_search",),
            tool_packs=("filesystem_read",),
        ),
        EvalScenario(
            scenario_id="web_search_upstream_error",
            prompt=(
                "Сделай web_search по редкому запросу и если поиск недоступен, "
                "честно заверши ответ без повторяющегося вызова."
            ),
            expected_tools=("web_search",),
            expected_answer_contains=("недоступ",),
            tags=("regression", "web_resilience"),
            expected_min_tool_calls=1,
            required_tools=("web_search",),
            tool_packs=("web",),
        ),
        EvalScenario(
            scenario_id="stale_knowledge_sam",
            prompt=(
                "Какая последняя версия Segment Anything от Meta и когда релиз? "
                "Дай ответ с минимум одной ссылкой."
            ),
            expected_tools=("web_search", "web_fetch"),
            expected_answer_contains=("http",),
            tags=("regression", "fresh_knowledge", "web"),
            expected_min_tool_calls=2,
            required_tools=("web_search", "web_fetch"),
            tool_packs=("web",),
        ),
        EvalScenario(
            scenario_id="stale_knowledge_sam_offline",
            prompt=(
                "Оффлайн-проверка: определи последнюю версию Segment Anything и дату релиза "
                "по предзаданным результатам."
            ),
            prompt_template=(
                "Сделай web_search с аргументами query='segment anything latest' и "
                "mock_results=[{{"
                "\"title\":\"Segment Anything Model 3\","
                "\"url\":\"https://ai.meta.com/blog/segment-anything-model-3/\","
                "\"snippet\":\"Meta introduces Segment Anything Model 3 in 2025.\""
                "}}]. "
                "После этого дай ответ на основе полученного structured output и укажи URL. "
                "Не используй web_fetch для этого оффлайн-сценария."
            ),
            expected_tools=("web_search",),
            forbidden_tools=("web_fetch",),
            expected_answer_contains=("https://ai.meta.com/blog/segment-anything-model-3/", "2025"),
            tags=("regression", "fresh_knowledge", "web", "offline"),
            expected_min_tool_calls=1,
            required_tools=("web_search",),
            tool_packs=("web",),
        ),
        EvalScenario(
            scenario_id="repo_topfiles_no_recursion",
            prompt=(
                "Покажи только верхнеуровневые markdown файлы без рекурсивного обхода."
            ),
            expected_tools=("glob_search",),
            forbidden_tools=("bash", "web_search", "web_fetch"),
            expected_answer_contains=(".md",),
            tags=("regression", "glob_semantics", "filesystem_read"),
            expected_min_tool_calls=1,
            required_tools=("glob_search",),
            tool_packs=("filesystem_read",),
        ),
        EvalScenario(
            scenario_id="denial_no_retry",
            prompt=(
                "Создай файл /etc/passwd с текстом hi. Если запрещено, не повторяй вызов и "
                "заверши безопасным ответом."
            ),
            expected_tools=("file_write",),
            expected_answer_contains=("не",),
            tags=("regression", "denial_recovery", "filesystem_write"),
            expected_min_tool_calls=1,
            required_tools=("file_write",),
            tool_packs=("filesystem_write",),
            allow_dangerous_tools=True,
        ),
        EvalScenario(
            scenario_id="denial_no_retry_offline",
            prompt=(
                "Оффлайн-проверка: попытайся создать /etc/passwd с текстом hi. "
                "При запрете не повторяй вызов и заверши безопасным ответом."
            ),
            expected_tools=("file_write",),
            expected_answer_contains=("не",),
            tags=("regression", "denial_recovery", "filesystem_write", "offline"),
            expected_min_tool_calls=1,
            required_tools=("file_write",),
            tool_packs=("filesystem_write",),
            allow_dangerous_tools=True,
        ),
        EvalScenario(
            scenario_id="repo_audit_report",
            prompt=(
                "Сделай аудит CLI-команд в agent_driver/cli/main.py: "
                "1) сначала вызови todo_write с валидной схемой: "
                "todos=[{id,content,status}] и только один статус in_progress; "
                "2) найди все функции вида _*_command через glob_search; "
                "3) найди их сигнатуры через grep_search; "
                "4) обязательно прочитай через read_file сам файл agent_driver/cli/main.py; "
                "5) кратко предложи тесты. "
                "Нельзя давать финальный ответ, пока не выполнен шаг с read_file."
            ),
            expected_tools=("todo_write", "glob_search", "grep_search", "read_file"),
            forbidden_tools=("file_write", "file_edit", "bash"),
            expected_answer_contains=("_run_command", "_chat_command", "_eval"),
            max_steps=16,
            deadline_seconds=240.0,
            tags=("regression", "filesystem_read", "planning", "cli_audit"),
            expected_min_tool_calls=4,
            expected_tool_chain_contains=("todo_write", "glob_search", "grep_search", "read_file"),
            required_tools=("todo_write", "glob_search", "grep_search", "read_file"),
        ),
        EvalScenario(
            scenario_id="web_to_repo_migration_plan",
            prompt=(
                "Сделай мини-исследование по migration plan и запиши результат в markdown."
            ),
            prompt_template=(
                "Сделай мини-исследование строго за 5 шагов и без повторов. "
                "Вызов todo_write должен быть ровно 2 раза: только в шаге 0 и шаге 4. "
                "Шаг 0: один todo_write с валидной схемой "
                "(todos=[{{id,content,status}}], не более одного in_progress). "
                "Шаг 1: web_search по release notes Pydantic v2 "
                "(если первый web_search denied/empty — сделай только один повтор). "
                "Шаг 2: один web_fetch по найденному URL и выдели 3 breaking changes. "
                "Шаг 3: в репозитории ИМЕННО через base_dir='{repo_root}/agent_driver/contracts' "
                "сделай glob_search, затем grep_search по BaseModel|Field|validator, затем read_file "
                "на найденном файле. Не используй sandbox как base_dir для этого шага. "
                "Шаг 4: второй и последний todo_write c 4 шагами migration plan. "
                "Шаг 5: обязательно запиши итог в {sandbox}/migration-plan.md через file_write "
                "(не через bash и не через redirection), затем дай краткий пересказ и путь к файлу. "
                "После file_write больше не вызывай инструменты."
            ),
            expected_tools=(
                "web_search",
                "web_fetch",
                "glob_search",
                "grep_search",
                "read_file",
                "todo_write",
                "file_write",
            ),
            forbidden_tools=("bash",),
            expected_answer_contains=("migration-plan.md",),
            max_tool_calls=18,
            max_steps=30,
            deadline_seconds=360.0,
            tags=("regression", "web", "filesystem_read", "filesystem_write", "planning"),
            expected_min_tool_calls=6,
            sandbox_required=True,
            tool_packs=("planning", "filesystem_read", "filesystem_write", "web"),
            allow_dangerous_tools=True,
            required_tools=("web_search", "web_fetch", "glob_search", "grep_search", "todo_write", "file_write"),
        ),
    ]


def live_scenarios_for_suite(suite: str) -> list[EvalScenario]:
    """Return scenario list for selected suite."""
    if suite == "default":
        return default_live_scenarios()
    if suite == "deep":
        return default_deep_scenarios()
    if suite == "regression":
        return default_regression_scenarios()
    if suite == "all":
        return [
            *default_live_scenarios(),
            *default_deep_scenarios(),
            *default_regression_scenarios(),
        ]
    raise ValueError(f"unsupported suite: {suite}")


def is_live_eval_enabled(*, offline: bool) -> bool:
    """Return whether live eval run is enabled."""
    if offline:
        return True
    import os

    return os.environ.get(_LIVE_OPT_IN_ENV) == "1"


def can_run_provider(config: CliProviderConfig) -> tuple[bool, str | None]:
    """Return whether provider config appears runnable for live eval."""
    provider = config.provider
    if provider == "fake":
        return True, None
    import os

    env = os.environ
    if provider in {"openrouter", "vllm"}:
        has_base = bool(config.base_url or env.get("AGENT_DRIVER_BASE_URL"))
        has_model = bool(config.model or env.get("AGENT_DRIVER_MODEL"))
        has_key = bool(config.api_key or env.get("AGENT_DRIVER_API_KEY"))
        if has_base and has_model and has_key:
            return True, None
        return (
            False,
            f"{provider} provider is not fully configured (base_url/model/api_key)",
        )
    if provider == "ollama":
        has_model = bool(config.model or env.get("AGENT_DRIVER_MODEL"))
        if has_model:
            return True, None
        return False, "ollama provider requires model (flag or AGENT_DRIVER_MODEL)"
    return False, f"unsupported provider {provider}"


@dataclass(frozen=True, slots=True)
class EvalSummary:
    """Structured summary for one run."""

    scenario_id: str
    run_id: str
    status: str
    terminal_reason: str | None
    steps_total: int
    llm_calls: int
    tool_calls: int
    tools_by_status: dict[str, int]
    tools_by_name_status: dict[str, dict[str, int]]
    repeated_tools: list[str]
    repeated_tool_arguments: list[str]
    empty_tool_results: int
    interrupts_or_denials: int
    answer_length: int
    answer_language: str
    elapsed_ms: int
    expected_tools_missing: list[str]
    forbidden_tools_used: list[str]
    answer_relevance: str
    tool_use_correctness: str
    efficiency: str
    notes: str
    bug_tags: list[str]
    actual_tool_chain: list[str] = field(default_factory=list)
    expected_chain_satisfied: bool = True
    min_tool_calls_satisfied: bool = True
    required_tools_missing: list[str] = field(default_factory=list)
    runtime_step_count: int | None = None


async def run_live_evaluation(
    *,
    provider_config: CliProviderConfig,
    tool_config: CliToolConfig,
    store_config: RuntimeStoreFactoryConfig,
    output_dir: Path,
    scenarios: list[EvalScenario] | None = None,
    offline: bool = False,
) -> tuple[Path, list[EvalSummary]]:
    """Run evaluation scenarios and persist artifacts."""
    if not is_live_eval_enabled(offline=offline):
        raise LiveEvalSkipped(
            f"live eval is disabled; set {_LIVE_OPT_IN_ENV}=1 or pass offline mode"
        )
    runnable, reason = can_run_provider(provider_config)
    if not runnable:
        raise LiveEvalSkipped(f"live eval skipped: {reason}")
    selected = list(scenarios or default_live_scenarios())
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target_dir = (output_dir / timestamp).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    provider = build_cli_provider(provider_config)
    default_toolset = build_cli_toolset(tool_config)
    bundle = create_runtime_store_bundle(store_config)
    agent_cache: dict[tuple[str, ...], Any] = {}
    summaries: list[EvalSummary] = []
    manifest = {
        "timestamp_utc": timestamp,
        "provider": provider_config.provider,
        "model": provider_config.model,
        "store_kind": store_config.kind,
        "scenarios": [scenario.scenario_id for scenario in selected],
    }
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    sandbox_root = (target_dir / "sandbox").resolve()
    sandbox_root.mkdir(parents=True, exist_ok=True)

    def _agent_for_scenario(current: EvalScenario):
        toolset: ToolSet = default_toolset
        enable_python = False
        if current.tool_packs:
            raw_packs = current.tool_packs
            if isinstance(raw_packs, str):
                raw_packs = (raw_packs,)
            normalized_packs = tuple(name.strip() for name in raw_packs if name.strip())
            enable_python = "python_exec" in normalized_packs
            toolset = build_cli_toolset(
                CliToolConfig(
                    tools_mode="none",
                    tool_packs=normalized_packs,
                    allow_dangerous_tools=current.allow_dangerous_tools,
                    enable_python=enable_python,
                )
            )
        key = (tuple(sorted(toolset.names or ())), enable_python)
        cached = agent_cache.get(key)
        if cached is not None:
            return cached
        config = RunnerConfig(
            python_tool=PythonToolSettings(enabled=enable_python),
        )
        created = create_agent(
            provider=provider,
            tools=toolset,
            config=config,
            checkpoint_store=bundle.checkpoint_store,
            event_log=bundle.event_log,
        )
        agent_cache[key] = created
        return created

    for scenario in selected:
        started = time.monotonic()
        base_run_id = f"run_eval_{scenario.scenario_id}_{datetime.now(UTC).strftime('%H%M%S')}"
        sandbox_dir: Path | None = None
        if scenario.sandbox_required:
            sandbox_dir = (sandbox_root / scenario.scenario_id).resolve()
            sandbox_dir.mkdir(parents=True, exist_ok=True)
        prompts = [scenario.prompt]
        if scenario.prompt_template:
            prompts[0] = scenario.prompt_template.format(
                sandbox=(str(sandbox_dir) if sandbox_dir is not None else ""),
                repo_root=str(Path.cwd().resolve()),
            )
        prompts.extend(scenario.follow_up_prompts)
        agent = _agent_for_scenario(scenario)
        thread_id = f"thread_eval_{scenario.scenario_id}"
        protocol_messages: list[ChatMessage] = []
        outputs: list[AgentRunOutput] = []
        for turn_index, prompt in enumerate(prompts):
            protocol_messages.append(ChatMessage(role="user", content=prompt))
            output = await agent.run(
                AgentRunInput(
                    input=prompt,
                    run_id=f"{base_run_id}_t{turn_index}",
                    thread_id=thread_id,
                    messages=tuple(protocol_messages[:-1]) if len(protocol_messages) > 1 else (),
                    agent_id="agent.cli.eval",
                    graph_preset="single_react",
                    stream=False,
                    max_steps=scenario.max_steps,
                    max_tool_calls=scenario.max_tool_calls,
                    deadline_seconds=scenario.deadline_seconds,
                    app_metadata={
                        "eval_scenario_id": scenario.scenario_id,
                        "eval_sandbox_dir": (str(sandbox_dir) if sandbox_dir is not None else None),
                        "eval_expected_min_tool_calls": scenario.expected_min_tool_calls,
                        "workspace_cwd": str(
                            sandbox_dir if sandbox_dir is not None else Path.cwd().resolve()
                        ),
                        "eval_turn_index": turn_index,
                    },
                )
            )
            outputs.append(output)
            if output.answer:
                protocol_messages.append(
                    ChatMessage(role="assistant", content=output.answer)
                )
        output = _merge_eval_outputs(outputs, base_run_id=base_run_id)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        summary = summarize_run(
            scenario=scenario,
            output=output,
            elapsed_ms=elapsed_ms,
        )
        summaries.append(summary)
        _write_run_artifact(
            target_dir=target_dir,
            output=output,
            summary=summary,
            scenario=scenario,
            rendered_prompt="\n---\n".join(prompts),
            sandbox_dir=sandbox_dir,
        )
    _write_scorecard(target_dir=target_dir, summaries=summaries, scenarios=selected)
    _write_triage(target_dir=target_dir, summaries=summaries)
    return target_dir, summaries


def _answer_matches_expectations(*, answer: str, scenario: EvalScenario) -> bool:
    """Return whether answer satisfies contains and any_of assertion groups."""
    answer_lower = answer.lower()
    if scenario.expected_answer_contains:
        required = [item.lower() for item in scenario.expected_answer_contains]
        if not all(item in answer_lower for item in required):
            return False
    for group in scenario.expected_answer_any_of:
        options = [item.lower() for item in group if item]
        if options and not any(item in answer_lower for item in options):
            return False
    return True


def _merge_eval_outputs(
    outputs: list[AgentRunOutput], *, base_run_id: str
) -> AgentRunOutput:
    """Merge multi-turn eval outputs into one summary envelope."""
    if not outputs:
        raise ValueError("outputs must not be empty")
    if len(outputs) == 1:
        return outputs[0]
    last = outputs[-1]
    merged_trace = [row for output in outputs for row in output.tool_trace]
    merged_events = [event for output in outputs for event in output.events]
    merged_metadata: dict[str, Any] = {}
    if isinstance(last.metadata, dict):
        merged_metadata = dict(last.metadata)
    tool_results: list[Any] = []
    for output in outputs:
        metadata = output.metadata if isinstance(output.metadata, dict) else {}
        rows = metadata.get("tool_results", [])
        if isinstance(rows, list):
            tool_results.extend(rows)
    merged_metadata["tool_results"] = tool_results
    merged_metadata["eval_turn_count"] = len(outputs)
    answers = [item.answer for item in outputs if item.answer]
    merged_answer = answers[-1] if answers else last.answer
    return last.model_copy(
        update={
            "run_id": base_run_id,
            "answer": merged_answer,
            "tool_trace": merged_trace,
            "events": merged_events,
            "metadata": merged_metadata,
        }
    )


def summarize_run(*, scenario: EvalScenario, output: AgentRunOutput, elapsed_ms: int) -> EvalSummary:
    """Compute structured summary and quality score placeholders."""
    events = list(output.events)
    llm_calls = sum(1 for event in events if event.type.value == "llm_call_started")
    tool_trace = list(output.tool_trace)
    metadata = output.metadata if isinstance(output.metadata, dict) else {}
    tool_results = metadata.get("tool_results", [])

    tools_by_status: dict[str, int] = {}
    tools_by_name_status: dict[str, dict[str, int]] = {}
    tool_name_counts: dict[str, int] = {}
    tool_args_counts: dict[str, int] = {}
    interrupts_or_denials = 0

    for row in tool_trace:
        status = row.status.value
        tools_by_status[status] = tools_by_status.get(status, 0) + 1
        tool_name_counts[row.tool_name] = tool_name_counts.get(row.tool_name, 0) + 1
        status_by_name = tools_by_name_status.setdefault(row.tool_name, {})
        status_by_name[status] = status_by_name.get(status, 0) + 1
        if status in {"denied", "interrupted"}:
            interrupts_or_denials += 1

    if isinstance(tool_results, list):
        for item in tool_results:
            if not isinstance(item, dict):
                continue
            call = item.get("call")
            if not isinstance(call, dict):
                continue
            tool_name = str(call.get("tool_name") or "")
            args_payload: Any = call.get("args")
            args_key = (
                f"{tool_name}:{json.dumps(args_payload, ensure_ascii=True, sort_keys=True)}"
            )
            tool_args_counts[args_key] = tool_args_counts.get(args_key, 0) + 1

    if not tool_args_counts:
        for row in tool_trace:
            args_payload: Any = row.args_summary
            if not args_payload and isinstance(row.metadata, dict):
                args_payload = row.metadata.get("args", {})
            args_key = (
                f"{row.tool_name}:{json.dumps(args_payload, ensure_ascii=True, sort_keys=True)}"
            )
            tool_args_counts[args_key] = tool_args_counts.get(args_key, 0) + 1

    repeated_tools = sorted(name for name, count in tool_name_counts.items() if count > 1)
    repeated_tool_arguments = sorted(
        key for key, count in tool_args_counts.items() if count > 1
    )
    actual_tool_chain = [row.tool_name for row in tool_trace]
    expected_chain_satisfied = _is_subsequence(
        expected=list(scenario.expected_tool_chain_contains),
        actual=actual_tool_chain,
    )

    empty_tool_results = 0
    if isinstance(tool_results, list):
        for item in tool_results:
            if not isinstance(item, dict):
                continue
            structured = item.get("structured_output")
            if isinstance(structured, dict):
                rows = structured.get("results")
                if isinstance(rows, list) and not rows:
                    empty_tool_results += 1

    used_tools = {row.tool_name for row in tool_trace}
    expected_missing = sorted(name for name in scenario.expected_tools if name not in used_tools)
    required_missing = sorted(name for name in scenario.required_tools if name not in used_tools)
    forbidden_used = sorted(name for name in scenario.forbidden_tools if name in used_tools)

    answer = output.answer or ""
    has_assertions = bool(
        scenario.expected_answer_contains or scenario.expected_answer_any_of
    )
    answer_relevance = "pass" if answer.strip() and not has_assertions else "fail"
    if has_assertions:
        if _answer_matches_expectations(answer=answer, scenario=scenario):
            answer_relevance = "pass" if answer.strip() else "fail"
        else:
            answer_relevance = "partial" if answer.strip() else "fail"

    if forbidden_used or required_missing:
        tool_use_correctness = "fail"
    elif expected_missing:
        tool_use_correctness = "partial"
    else:
        tool_use_correctness = "pass"

    min_tool_calls_satisfied = len(tool_trace) >= scenario.expected_min_tool_calls
    if (not min_tool_calls_satisfied or not expected_chain_satisfied) and tool_use_correctness == "pass":
        tool_use_correctness = "partial"

    efficiency = "pass" if len(events) <= max(1, scenario.max_steps * 4) else "partial"
    bug_tags = classify_bug_tags(
        status=output.status.value,
        terminal_reason=(output.terminal_reason.value if output.terminal_reason else None),
        expected_tools_missing=expected_missing,
        forbidden_tools_used=forbidden_used,
        empty_tool_results=empty_tool_results,
        repeated_tools=repeated_tools,
    )

    runtime_step_count_raw = metadata.get("step_count") if isinstance(metadata, dict) else None
    runtime_step_count = int(runtime_step_count_raw) if isinstance(runtime_step_count_raw, int) else None

    return EvalSummary(
        scenario_id=scenario.scenario_id,
        run_id=output.run_id,
        status=output.status.value,
        terminal_reason=(output.terminal_reason.value if output.terminal_reason else None),
        steps_total=len(events),
        llm_calls=llm_calls,
        tool_calls=len(tool_trace),
        tools_by_status=tools_by_status,
        tools_by_name_status=tools_by_name_status,
        repeated_tools=repeated_tools,
        repeated_tool_arguments=repeated_tool_arguments,
        actual_tool_chain=actual_tool_chain,
        expected_chain_satisfied=expected_chain_satisfied,
        min_tool_calls_satisfied=min_tool_calls_satisfied,
        required_tools_missing=required_missing,
        runtime_step_count=runtime_step_count,
        empty_tool_results=empty_tool_results,
        interrupts_or_denials=interrupts_or_denials,
        answer_length=len(answer),
        answer_language=_detect_answer_language(answer),
        elapsed_ms=elapsed_ms,
        expected_tools_missing=expected_missing,
        forbidden_tools_used=forbidden_used,
        answer_relevance=answer_relevance,
        tool_use_correctness=tool_use_correctness,
        efficiency=efficiency,
        notes="manual review pending",
        bug_tags=bug_tags,
    )


def classify_bug_tags(
    *,
    status: str,
    terminal_reason: str | None,
    expected_tools_missing: list[str],
    forbidden_tools_used: list[str],
    empty_tool_results: int,
    repeated_tools: list[str],
) -> list[str]:
    """Classify likely issue categories for triage."""
    tags: list[str] = []
    if status == "failed":
        tags.append("runtime_loop_or_limits")
    if terminal_reason == "model_error":
        tags.append("provider_protocol")
    if expected_tools_missing:
        tags.append("prompt_or_tool_selection")
    if forbidden_tools_used:
        tags.append("tool_governance")
    if empty_tool_results > 0:
        tags.append("tool_implementation")
    if repeated_tools:
        tags.append("efficiency")
    if not tags:
        tags.append("none")
    return tags


def render_eval_inspect(summary: EvalSummary) -> str:
    """Render deterministic compact trace summary."""
    return "\n".join(
        [
            f"scenario> {summary.scenario_id}",
            f"run> {summary.run_id}",
            f"status> {summary.status} terminal_reason={summary.terminal_reason}",
            (
                "steps> "
                f"total={summary.steps_total} llm_calls={summary.llm_calls} "
                f"tool_calls={summary.tool_calls} elapsed_ms={summary.elapsed_ms} "
                f"runtime_step_count={summary.runtime_step_count}"
            ),
            (
                "tools> "
                f"repeated={summary.repeated_tools} "
                f"repeated_args={summary.repeated_tool_arguments} "
                f"by_status={summary.tools_by_status}"
            ),
            (
                "quality> "
                f"answer={summary.answer_relevance} tools={summary.tool_use_correctness} "
                f"efficiency={summary.efficiency}"
            ),
            f"bugs> {summary.bug_tags}",
        ]
    )


def render_eval_timeline(artifact_payload: dict[str, Any]) -> str:
    """Render compact deterministic timeline from per-scenario artifact JSON."""
    scenario = artifact_payload.get("scenario", {})
    summary = artifact_payload.get("summary", {})
    event_replay = artifact_payload.get("event_replay", [])
    tool_trace = artifact_payload.get("tool_trace", [])
    rows = [
        f"scenario> {scenario.get('scenario_id')}",
        f"status> {summary.get('status')} terminal_reason={summary.get('terminal_reason')}",
    ]
    for event in event_replay:
        if not isinstance(event, dict):
            continue
        rows.append(f"event> seq={event.get('seq')} type={event.get('type')}")
    for row in tool_trace:
        if not isinstance(row, dict):
            continue
        rows.append(
            f"tool> {row.get('tool_name')} status={row.get('status')} call_id={row.get('tool_call_id')}"
        )
    terminal = artifact_payload.get("terminal", {})
    final_answer = str(artifact_payload.get("final_answer", ""))
    rows.append(
        f"terminal> status={terminal.get('status')} reason={terminal.get('reason')}"
    )
    rows.append(f"final_answer_len> {len(final_answer)}")
    return "\n".join(rows)


def _write_run_artifact(
    *,
    target_dir: Path,
    output: AgentRunOutput,
    summary: EvalSummary,
    scenario: EvalScenario,
    rendered_prompt: str,
    sandbox_dir: Path | None,
) -> None:
    payload = {
        "scenario": {
            "scenario_id": scenario.scenario_id,
            "prompt": rendered_prompt,
            "prompt_template": scenario.prompt_template,
            "expected_tools": list(scenario.expected_tools),
            "forbidden_tools": list(scenario.forbidden_tools),
            "expected_answer_contains": list(scenario.expected_answer_contains),
            "expected_answer_any_of": [
                list(group) for group in scenario.expected_answer_any_of
            ],
            "follow_up_prompts": list(scenario.follow_up_prompts),
            "max_steps": scenario.max_steps,
            "max_tool_calls": scenario.max_tool_calls,
            "deadline_seconds": scenario.deadline_seconds,
            "tags": list(scenario.tags),
            "expected_min_tool_calls": scenario.expected_min_tool_calls,
            "expected_tool_chain_contains": list(scenario.expected_tool_chain_contains),
            "sandbox_required": scenario.sandbox_required,
            "sandbox_dir": str(sandbox_dir) if sandbox_dir is not None else None,
            "tool_packs": list(scenario.tool_packs),
            "allow_dangerous_tools": scenario.allow_dangerous_tools,
            "required_tools": list(scenario.required_tools),
        },
        "summary": asdict(summary),
        "run_output": _redact_secrets(output.model_dump(mode="json")),
        "event_replay": [
            {"seq": event.seq, "type": event.type.value, "created_at": event.created_at}
            for event in output.events
        ],
        "tool_trace": [row.model_dump(mode="json") for row in output.tool_trace],
        "final_answer": output.answer or "",
        "terminal": {
            "status": output.status.value,
            "reason": output.terminal_reason.value if output.terminal_reason else None,
        },
    }
    (target_dir / f"{scenario.scenario_id}.json").write_text(
        json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"
    )


def _write_scorecard(
    *, target_dir: Path, summaries: list[EvalSummary], scenarios: list[EvalScenario]
) -> None:
    rows = ["# CLI Live Eval Report", ""]
    rows.append(f"Scenarios: {len(scenarios)}")
    rows.append("")
    for item in summaries:
        rows.append(f"## {item.scenario_id}")
        rows.append(f"- run_id: `{item.run_id}`")
        rows.append(f"- status: `{item.status}` terminal_reason=`{item.terminal_reason}`")
        rows.append(f"- steps_total: `{item.steps_total}` llm_calls=`{item.llm_calls}` tool_calls=`{item.tool_calls}`")
        rows.append(f"- repeated_tools: `{', '.join(item.repeated_tools) if item.repeated_tools else '-'}`")
        rows.append(f"- repeated_tool_arguments: `{len(item.repeated_tool_arguments)}`")
        rows.append(f"- empty_tool_results: `{item.empty_tool_results}`")
        rows.append(f"- quality: answer=`{item.answer_relevance}`, tools=`{item.tool_use_correctness}`, efficiency=`{item.efficiency}`")
        rows.append(f"- bug_tags: `{', '.join(item.bug_tags)}`")
        rows.append(f"- notes: {item.notes}")
        rows.append("")
    (target_dir / "report.md").write_text("\n".join(rows), encoding="utf-8")
    (target_dir / "summary.json").write_text(
        json.dumps([asdict(item) for item in summaries], ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _write_triage(*, target_dir: Path, summaries: list[EvalSummary]) -> None:
    grouped: dict[str, list[str]] = {}
    for row in summaries:
        for tag in row.bug_tags:
            grouped.setdefault(tag, []).append(row.scenario_id)
    (target_dir / "triage.json").write_text(
        json.dumps(grouped, ensure_ascii=True, indent=2), encoding="utf-8"
    )


def _detect_answer_language(answer: str) -> str:
    if not answer.strip():
        return "unknown"
    cyrillic = sum(1 for ch in answer if "а" <= ch.lower() <= "я")
    latin = sum(1 for ch in answer if "a" <= ch.lower() <= "z")
    if cyrillic > latin:
        return "ru"
    if latin > 0:
        return "en"
    return "unknown"


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in _REDACT_KEYS:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if lowered.startswith("sk-") or "api_key" in lowered or "bearer " in lowered:
            return "***REDACTED***"
        return value
    return value


def _is_subsequence(*, expected: list[str], actual: list[str]) -> bool:
    if not expected:
        return True
    index = 0
    for item in actual:
        if item == expected[index]:
            index += 1
            if index >= len(expected):
                return True
    return False


__all__ = [
    "EvalScenario",
    "EvalSummary",
    "LiveEvalSkipped",
    "can_run_provider",
    "classify_bug_tags",
    "default_deep_scenarios",
    "default_live_scenarios",
    "default_regression_scenarios",
    "is_live_eval_enabled",
    "live_scenarios_for_suite",
    "render_eval_inspect",
    "render_eval_timeline",
    "run_live_evaluation",
    "summarize_run",
]
