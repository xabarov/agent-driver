"""Tests for packaged product CLI commands."""

from __future__ import annotations

import json
import importlib

from agent_driver.cli.sessions import SessionStore

cli_main = importlib.import_module("agent_driver.cli.main")

main = cli_main.main


def _parse_run_summary(output: str) -> dict[str, str]:
    lines = [line for line in output.strip().splitlines() if line.strip()]
    return json.loads(lines[-1])


def test_cli_run_and_replay_with_sqlite_store(tmp_path, capsys) -> None:
    """Run command should persist events that replay can read."""
    sqlite_path = tmp_path / "runtime.sqlite3"
    exit_code = main(
        [
            "run",
            "hello world",
            "--provider",
            "fake",
            "--plain",
            "--store-kind",
            "sqlite",
            "--sqlite-path",
            str(sqlite_path),
            "--run-id",
            "run_cli_test_1",
        ]
    )
    assert exit_code == 0
    run_output = capsys.readouterr().out
    summary = _parse_run_summary(run_output)
    assert summary["run_id"] == "run_cli_test_1"

    replay_code = main(
        [
            "replay",
            "--run-id",
            "run_cli_test_1",
            "--store-kind",
            "sqlite",
            "--sqlite-path",
            str(sqlite_path),
        ]
    )
    assert replay_code == 0
    replay_output = capsys.readouterr().out
    assert "[0001] run_started:" in replay_output
    assert "run_completed" in replay_output


def test_cli_tail_and_tree_with_sqlite_store(tmp_path, capsys) -> None:
    """Tail and tree commands should render persisted run views."""
    sqlite_path = tmp_path / "runtime.sqlite3"
    _ = main(
        [
            "run",
            "tail tree",
            "--provider",
            "fake",
            "--plain",
            "--store-kind",
            "sqlite",
            "--sqlite-path",
            str(sqlite_path),
            "--run-id",
            "run_cli_test_2",
        ]
    )
    _ = capsys.readouterr()

    tail_code = main(
        [
            "tail",
            "--run-id",
            "run_cli_test_2",
            "--last-n",
            "2",
            "--store-kind",
            "sqlite",
            "--sqlite-path",
            str(sqlite_path),
        ]
    )
    assert tail_code == 0
    tail_output = capsys.readouterr().out.strip().splitlines()
    assert len(tail_output) == 2

    tree_code = main(
        [
            "tree",
            "--run-id",
            "run_cli_test_2",
            "--store-kind",
            "sqlite",
            "--sqlite-path",
            str(sqlite_path),
        ]
    )
    assert tree_code == 0
    tree_output = capsys.readouterr().out
    assert "run_started:" in tree_output
    assert "run_completed:" in tree_output


def test_cli_tail_follow_exits_for_completed_run(tmp_path, capsys) -> None:
    """Follow mode should exit immediately when run already has terminal event."""
    sqlite_path = tmp_path / "runtime.sqlite3"
    _ = main(
        [
            "run",
            "follow done",
            "--provider",
            "fake",
            "--plain",
            "--store-kind",
            "sqlite",
            "--sqlite-path",
            str(sqlite_path),
            "--run-id",
            "run_cli_test_3",
        ]
    )
    _ = capsys.readouterr()

    tail_code = main(
        [
            "tail",
            "--run-id",
            "run_cli_test_3",
            "--follow",
            "--store-kind",
            "sqlite",
            "--sqlite-path",
            str(sqlite_path),
        ]
    )
    assert tail_code == 0
    output = capsys.readouterr().out
    assert "[0001] run_started:" in output


def test_cli_chat_applies_default_runtime_bounds(monkeypatch) -> None:
    """Chat command should pass safe default budgets into run input."""
    captured: dict[str, object] = {}

    async def _fake_chat_command(args):
        captured["max_steps"] = args.max_steps
        captured["max_tool_calls"] = args.max_tool_calls
        captured["deadline_seconds"] = args.deadline_seconds
        return 0

    monkeypatch.setattr(cli_main, "_chat_command", _fake_chat_command)
    assert cli_main.main(["chat", "--plain", "--provider", "fake"]) == 0
    assert captured["max_steps"] == 8
    assert captured["max_tool_calls"] == 4
    assert captured["deadline_seconds"] == 60.0


def test_cli_chat_keyboard_interrupt_returns_130(monkeypatch, capsys) -> None:
    """Top-level chat command should hide traceback on KeyboardInterrupt."""

    async def _raise_interrupt(_args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli_main, "_chat_command", _raise_interrupt)
    code = cli_main.main(["chat", "--plain", "--provider", "fake"])
    assert code == 130
    output = capsys.readouterr().out
    assert "chat> interrupted" in output


def test_cli_config_show_outputs_json(tmp_path, monkeypatch, capsys) -> None:
    """Config show should return resolved config JSON."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_DRIVER_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_DRIVER_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_DRIVER_MODEL", raising=False)
    monkeypatch.delenv("AGENT_DRIVER_API_KEY", raising=False)
    (tmp_path / ".agent-driver.toml").write_text(
        "[cli]\nprovider='fake'\nmax_steps=9\n",
        encoding="utf-8",
    )
    code = main(["config", "show"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "fake"
    assert payload["max_steps"] == 9


def test_cli_explicit_flag_overrides_config(tmp_path, monkeypatch) -> None:
    """Explicit CLI flags should win over config defaults."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent-driver.toml").write_text(
        "[cli]\nprovider='openrouter'\n",
        encoding="utf-8",
    )
    args = cli_main._build_parser().parse_args(["chat", "--provider", "fake"])  # pylint: disable=protected-access
    resolved = cli_main._resolve_args_with_config_and_explicit(  # pylint: disable=protected-access
        args, explicit_options={"--provider"}
    )
    assert resolved.provider == "fake"


def test_cli_defaults_to_openrouter_chat(monkeypatch, tmp_path) -> None:
    """Bare chat command should resolve to OpenRouter-oriented defaults."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_DRIVER_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_DRIVER_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_DRIVER_MODEL", raising=False)

    args = cli_main._build_parser().parse_args(["chat"])  # pylint: disable=protected-access
    resolved = cli_main._resolve_args_with_config_and_explicit(  # pylint: disable=protected-access
        args, explicit_options=set()
    )

    assert resolved.provider == "openrouter"
    assert resolved.base_url == "https://openrouter.ai/api/v1"
    assert resolved.model == "openai/gpt-5.4"
    assert resolved.tools == "default"


def test_cli_loads_project_dotenv_for_openrouter(monkeypatch, tmp_path) -> None:
    """Local .env should make bare chat command usable in repo checkout."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_DRIVER_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_DRIVER_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_DRIVER_MODEL", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "AGENT_DRIVER_PROVIDER=openrouter",
                "AGENT_DRIVER_API_KEY=test-key",
                "AGENT_DRIVER_BASE_URL=https://openrouter.ai/api/v1",
                "AGENT_DRIVER_MODEL=openai/test-model",
            ]
        ),
        encoding="utf-8",
    )

    args = cli_main._build_parser().parse_args(["chat"])  # pylint: disable=protected-access
    resolved = cli_main._resolve_args_with_config_and_explicit(  # pylint: disable=protected-access
        args, explicit_options=set()
    )

    assert resolved.provider == "openrouter"
    assert resolved.base_url == "https://openrouter.ai/api/v1"
    assert resolved.model == "openai/test-model"


def test_cli_inspect_and_export_commands(tmp_path, capsys) -> None:
    """Inspect and export commands should render persisted run data."""
    sqlite_path = tmp_path / "runtime.sqlite3"
    _ = main(
        [
            "run",
            "inspect me",
            "--provider",
            "fake",
            "--plain",
            "--store-kind",
            "sqlite",
            "--sqlite-path",
            str(sqlite_path),
            "--run-id",
            "run_cli_inspect_1",
        ]
    )
    _ = capsys.readouterr()
    inspect_code = main(
        [
            "inspect",
            "--run-id",
            "run_cli_inspect_1",
            "--format",
            "json",
            "--store-kind",
            "sqlite",
            "--sqlite-path",
            str(sqlite_path),
        ]
    )
    assert inspect_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list) and payload

    export_path = tmp_path / "run_cli_inspect_1.jsonl"
    export_code = main(
        [
            "export",
            "--run-id",
            "run_cli_inspect_1",
            "--format",
            "jsonl",
            "--output",
            str(export_path),
            "--store-kind",
            "sqlite",
            "--sqlite-path",
            str(sqlite_path),
        ]
    )
    assert export_code == 0
    assert export_path.exists()
    assert export_path.read_text(encoding="utf-8").strip()


def test_cli_sessions_list_and_show(tmp_path, monkeypatch, capsys) -> None:
    """Sessions command should list and show persisted session metadata."""
    monkeypatch.chdir(tmp_path)
    store = SessionStore()
    store.upsert(
        session_id="session_1",
        thread_id="thread_1",
        run_ids=["run_1"],
        transcript=[("user", "hi"), ("assistant", "ok")],
    )
    list_code = main(["sessions", "list"])
    assert list_code == 0
    assert "session_1" in capsys.readouterr().out

    show_code = main(["sessions", "show", "--session-id", "session_1"])
    assert show_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == "session_1"


def test_cli_doctor_command_monkeypatched(monkeypatch) -> None:
    """Main should dispatch doctor command."""

    async def _fake_doctor(_args):
        return 0

    monkeypatch.setattr(cli_main, "_doctor_command", _fake_doctor)
    assert cli_main.main(["doctor", "--provider", "fake"]) == 0


def test_cli_resume_command_monkeypatched(monkeypatch) -> None:
    """Main should dispatch resume command."""

    async def _fake_resume(_args):
        return 0

    monkeypatch.setattr(cli_main, "_resume_command", _fake_resume)
    assert (
        cli_main.main(
            [
                "resume",
                "approve",
                "--run-id",
                "run_1",
                "--interrupt-id",
                "interrupt_1",
                "--provider",
                "fake",
            ]
        )
        == 0
    )
