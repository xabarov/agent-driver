import asyncio
from pathlib import Path
import os

from agent_driver.contracts import AgentRunInput
from agent_driver.adapters import (
    cli_replay_lines,
    cli_run_live_lines,
    is_rich_available,
    render_cli_line,
)
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet

from httpx import HTTPStatusError


def _load_local_dotenv() -> None:
    dotenv_path = Path(__file__).resolve().parents[1] / ".env"
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


async def main() -> int:
    _load_local_dotenv()
    api_key = os.getenv("AGENT_DRIVER_OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    base_url = os.getenv("AGENT_DRIVER_OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.getenv("AGENT_DRIVER_OPENAI_MODEL", "openai/gpt-4o-mini")
    if not api_key:
        print(
            "Missing API key. Set AGENT_DRIVER_OPENAI_API_KEY "
            "or OPENROUTER_API_KEY in environment/.env."
        )
        return 2
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter",
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
    )

    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("web_search"),  # только web_search в tool surface
    )

    run_input = AgentRunInput(
        input="Find 3 recent updates about Python 3.13 and summarize briefly.",
        run_id="run_websearch_terminal_1",
        agent_id="agent.cli",
        graph_preset="single_react",
        stream=True,  # важно: чтобы видеть live events
    )

    prefer_rich = is_rich_available()
    print(f"Live log mode: {'rich' if prefer_rich else 'plain-text'}")
    try:
        async for line in cli_run_live_lines(
            agent.stream(run_input),
            prefer_rich=prefer_rich,
        ):
            print(line)
    except HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        print(f"Provider request failed (status={status}).")
        if status == 401:
            print("Unauthorized: check AGENT_DRIVER_OPENAI_API_KEY / OPENROUTER_API_KEY.")
        else:
            print(str(exc))
        return 1
    except Exception as exc:  # pragma: no cover - manual smoke path
        print(f"Run failed: {exc}")
        return 1

    # Replay тех же событий из event log
    print("\n=== Replay ===")
    for line in cli_replay_lines(
        agent.runner.deps.event_log, run_id="run_websearch_terminal_1"
    ):
        print(line)
    if not prefer_rich:
        print("\nTip: install optional CLI UX package for rich logs:")
        print("  uv sync --extra cli --extra dev")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))