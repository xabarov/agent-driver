"""Tests for packaged product CLI commands."""

from __future__ import annotations

import asyncio
import json
import importlib
import shlex
import sys
from types import SimpleNamespace

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
    assert captured["max_steps"] == 24
    assert captured["max_tool_calls"] == 12
    assert captured["deadline_seconds"] == 180.0


def test_cli_run_and_chat_accept_workspace_option(tmp_path) -> None:
    parser = cli_main._build_parser()  # pylint: disable=protected-access
    run_args = parser.parse_args(["run", "hello", "--workspace", str(tmp_path)])
    chat_args = parser.parse_args(["chat", "--workspace", str(tmp_path)])
    assert run_args.workspace == str(tmp_path)
    assert chat_args.workspace == str(tmp_path)


def test_cli_capability_pack_dry_run_writes_evidence_index(tmp_path, capsys) -> None:
    output_dir = tmp_path / "pack-dry-run"

    code = main(
        [
            "capability-pack",
            "dry-run",
            "--pack-id",
            "excel_workbook_chat",
            "--scenario-id",
            "excel.workbook_context.transaction.v1",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry_run"
    assert payload["executed_commands"] == []
    resolution = payload["capability_pack_resolution"]
    assert resolution["pack_id"] == "excel_workbook_chat"
    assert resolution["gate_statuses"]["openrouter_live_preflight"] == "skipped"
    assert (output_dir / "manifest.json").is_file()
    assert (output_dir / "evidence_index.json").is_file()
    assert (output_dir / "capability_pack_resolution.json").is_file()
    assert (output_dir / "capability_pack_dry_run.json").is_file()
    evidence_index = json.loads(
        (output_dir / "evidence_index.json").read_text(encoding="utf-8")
    )
    assert evidence_index["pack_id"] == "excel_workbook_chat"
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert {row["artifact_type"] for row in manifest["artifacts"]} == {
        "evidence_index",
        "capability_pack_resolution",
        "capability_pack_dry_run",
    }


def test_cli_capability_pack_dry_run_rejects_mismatched_scenario(capsys) -> None:
    code = main(
        [
            "capability-pack",
            "dry-run",
            "--pack-id",
            "deep_research_chat_demo",
            "--adapter-id",
            "chat_demo",
            "--scenario-id",
            "excel.workbook_context.transaction.v1",
        ]
    )

    assert code == 2
    output = capsys.readouterr().out
    assert "capability-pack error:" in output
    assert "belongs to adapter" in output


def test_cli_harness_adapter_compat_writes_report(tmp_path, capsys) -> None:
    evidence_dir = tmp_path / "adapter-evidence"
    evidence_dir.mkdir()
    (evidence_dir / "evidence_index.json").write_text(
        json.dumps(
            {
                "index_id": "adapter-cli",
                "pack_id": "deep_research_chat_demo",
                "scenario_ids": ["harness_adapter.chat_demo.deep_research.v1"],
                "gates": [
                    {
                        "gate_id": "deterministic_tests",
                        "status": "passed",
                        "evidence_path": "adapter_compatibility_report.json",
                    },
                    {
                        "gate_id": "phoenix_trace",
                        "status": "not_run",
                        "reason": "no_live_mode",
                    },
                ],
                "artifacts": [
                    {
                        "artifact_id": "adapter_compatibility_report.json",
                        "artifact_type": "adapter_compatibility_report",
                        "path": "adapter_compatibility_report.json",
                        "gate_id": "deterministic_tests",
                    }
                ],
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "adapter-report"

    code = main(
        [
            "harness-adapter",
            "compat",
            "--adapter",
            "chat_demo",
            "--evidence-index-dir",
            str(evidence_dir),
            "--no-live",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["adapter_id"] == "chat_demo"
    assert payload["feature_statuses"]["live_gates"] == "no_claim"
    assert payload["validation_gate_statuses"]["phoenix_trace"] == "no_claim"
    assert (output_dir / "adapter_compatibility_report.json").is_file()
    assert (output_dir / "adapter_compatibility_report.md").is_file()


def test_cli_capability_pack_run_deterministic_writes_command_outputs(
    tmp_path, capsys
) -> None:
    output_dir = tmp_path / "pack-run"
    command = f"{shlex.quote(sys.executable)} -c \"print('cli capability ok')\""

    code = main(
        [
            "capability-pack",
            "run-deterministic",
            "--pack-id",
            "deep_research_chat_demo",
            "--scenario-id",
            "chat_demo.deep_research.source_report.v1",
            "--deterministic-command",
            command,
            "--cwd",
            str(tmp_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "run_deterministic"
    assert payload["executed_commands"][0]["status"] == "passed"
    gate_statuses = {
        row["gate_id"]: row["status"] for row in payload["validation_gate_results"]
    }
    assert gate_statuses["deterministic_tests"] == "passed"
    assert gate_statuses["support_bundle_artifact"] == "passed"
    assert (
        payload["capability_pack_resolution"]["gate_statuses"][
            "support_bundle_artifact"
        ]
        == "passed"
    )
    assert (output_dir / "command_outputs" / "deterministic_1.json").is_file()
    assert (output_dir / "evidence_index.json").is_file()
    validation_gates = json.loads(
        (output_dir / "validation_gates.json").read_text(encoding="utf-8")
    )
    assert validation_gates["statuses"]["support_bundle_artifact"] == "passed"
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "command_output" in {row["artifact_type"] for row in manifest["artifacts"]}


def test_cli_capability_pack_run_deterministic_blocks_template_command(capsys) -> None:
    code = main(
        [
            "capability-pack",
            "run-deterministic",
            "--pack-id",
            "excel_workbook_chat",
            "--scenario-id",
            "excel.workbook_context.transaction.v1",
            "--deterministic-command",
            "python -m pytest backend/tests/<test>.py",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["executed_commands"][0]["status"] == "blocked"
    assert payload["validation_gate_results"][0]["status"] == "blocked"


def test_cli_capability_pack_audit_writes_validation_reports(tmp_path, capsys) -> None:
    run_dir = tmp_path / "pack-run"
    audit_dir = tmp_path / "pack-audit"
    command = f"{shlex.quote(sys.executable)} -c \"print('audit cli ok')\""
    assert (
        main(
            [
                "capability-pack",
                "run-deterministic",
                "--pack-id",
                "excel_workbook_chat",
                "--scenario-id",
                "excel.workbook_context.transaction.v1",
                "--deterministic-command",
                command,
                "--cwd",
                str(tmp_path),
                "--output-dir",
                str(run_dir),
            ]
        )
        == 0
    )
    _ = capsys.readouterr()

    code = main(
        [
            "capability-pack",
            "audit",
            "--evidence-index-dir",
            str(run_dir),
            "--no-live",
            "--strict",
            "--output-dir",
            str(audit_dir),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["regression_summary"]["candidate_status"] == "no_claim"
    assert payload["strict_passed"] is True
    assert (audit_dir / "validation_run.json").is_file()
    assert (audit_dir / "validation_report.md").is_file()
    report = (audit_dir / "validation_report.md").read_text(encoding="utf-8")
    assert "openrouter_live_preflight" in report


def test_cli_skills_lifecycle_audit_writes_auditable_artifacts(
    tmp_path, capsys
) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "research"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
id: skill.research
name: research
description: Research skill
allowed_tools: [web_search]
product_families: [chat_demo]
---
# Research
body should not appear in artifacts
""",
        encoding="utf-8",
    )
    output_dir = tmp_path / "skills-lifecycle"

    code = main(
        [
            "skills-lifecycle",
            "audit",
            "--scenario",
            "skills_lifecycle.chat_demo_research_skills.v1",
            "--skills-dir",
            str(skills_dir),
            "--no-live",
            "--output-dir",
            str(output_dir),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["mode"] == "deterministic"
    assert payload["report"]["usage_summary"]["discovered"] == 1
    assert payload["redaction"]["contains_raw_skill_body"] is False
    assert (output_dir / "skills_inventory_snapshot.json").is_file()
    assert (output_dir / "skills_lock.json").is_file()
    assert (output_dir / "skills_reload_diff.json").is_file()
    assert (output_dir / "skills_compatibility_report.json").is_file()
    assert (output_dir / "skills_compatibility_report.md").is_file()
    assert (output_dir / "evidence_index.json").is_file()
    assert "body should not appear" not in (
        output_dir / "skills_compatibility_report.json"
    ).read_text(encoding="utf-8")

    audit_dir = tmp_path / "skills-audit"
    audit_code = main(
        [
            "capability-pack",
            "audit",
            "--evidence-index-dir",
            str(output_dir),
            "--no-live",
            "--strict",
            "--output-dir",
            str(audit_dir),
        ]
    )
    audit = json.loads(capsys.readouterr().out)
    assert audit_code == 0
    assert audit["strict_passed"] is True
    assert (
        "skills_lifecycle.chat_demo_research_skills.v1"
        in audit["validation_run"]["scenario_ids"]
    )
    assert (audit_dir / "validation_report.md").is_file()


def test_cli_capability_pack_audit_strict_fails_missing_required_gates(
    tmp_path, capsys
) -> None:
    run_dir = tmp_path / "pack-dry-run"
    assert (
        main(
            [
                "capability-pack",
                "dry-run",
                "--pack-id",
                "deep_research_chat_demo",
                "--scenario-id",
                "chat_demo.deep_research.source_report.v1",
                "--output-dir",
                str(run_dir),
            ]
        )
        == 0
    )
    _ = capsys.readouterr()

    code = main(
        [
            "capability-pack",
            "audit",
            "--evidence-index-dir",
            str(run_dir),
            "--no-live",
            "--strict",
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["regression_summary"]["candidate_status"] == "failed"
    assert payload["regression_summary"]["skipped_required_gates"] == [
        "deterministic_tests",
        "support_bundle_artifact",
    ]


def test_cli_chat_keyboard_interrupt_returns_130(monkeypatch, capsys) -> None:
    """Top-level chat command should hide traceback on KeyboardInterrupt."""

    async def _raise_interrupt(_args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli_main, "_chat_command", _raise_interrupt)
    code = cli_main.main(["chat", "--plain", "--provider", "fake"])
    assert code == 130
    output = capsys.readouterr().out
    assert "chat> interrupted" in output


def test_chat_command_wires_memory_and_permission_flags(monkeypatch) -> None:
    """--memory sqlite + --permission-mode build a provider and a tool gate."""
    captured: dict[str, object] = {}

    def _fake_store_bundle(_config):
        return SimpleNamespace(checkpoint_store=object(), event_log=object())

    async def _fake_provider_healthcheck():
        return SimpleNamespace(
            provider_name="fake", healthy=True, configured=True, latency_ms=1.0
        )

    fake_provider = SimpleNamespace(name="fake", healthcheck=_fake_provider_healthcheck)

    def _fake_create_agent(**kwargs):
        captured["memory_provider"] = kwargs.get("memory_provider")

        class _Registry:
            @staticmethod
            def list_registered():
                return []

        fake_runner = SimpleNamespace(deps=SimpleNamespace(tool_registry=_Registry()))
        return SimpleNamespace(runner=fake_runner)

    async def _fake_run_chat_session(**kwargs):
        captured["tool_gate"] = kwargs.get("tool_gate")
        return 0

    monkeypatch.setattr(cli_main, "create_runtime_store_bundle", _fake_store_bundle)
    monkeypatch.setattr(cli_main, "build_cli_provider", lambda _cfg: fake_provider)
    monkeypatch.setattr(
        cli_main, "build_cli_toolset", lambda _cfg: SimpleNamespace(names=())
    )
    monkeypatch.setattr(cli_main, "create_agent", _fake_create_agent)
    monkeypatch.setattr(cli_main, "run_chat_session", _fake_run_chat_session)

    args = cli_main._build_parser().parse_args(  # pylint: disable=protected-access
        [
            "chat",
            "--provider",
            "fake",
            "--plain",
            "--memory",
            "sqlite",
            "--memory-path",
            ":memory:",
            "--permission-mode",
            "strict",
        ]
    )
    resolved = cli_main._resolve_args_with_config_and_explicit(  # pylint: disable=protected-access
        args, explicit_options={"--provider", "--plain"}
    )
    code = asyncio.run(cli_main._chat_command(resolved))  # pylint: disable=protected-access
    assert code == 0
    # A memory provider was constructed and handed to the agent.
    assert type(captured["memory_provider"]).__name__ == "StoreBackedMemoryProvider"
    # A permission gate (callable ToolGate) reached the chat loop.
    assert callable(captured["tool_gate"])


def test_chat_command_passes_capability_pack_metadata(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_store_bundle(_config):
        return SimpleNamespace(checkpoint_store=object(), event_log=object())

    async def _fake_provider_healthcheck():
        return SimpleNamespace(
            provider_name="fake", healthy=True, configured=True, latency_ms=1.0
        )

    fake_provider = SimpleNamespace(name="fake", healthcheck=_fake_provider_healthcheck)

    def _fake_create_agent(**_kwargs):
        class _Registry:
            @staticmethod
            def list_registered():
                return []

        fake_runner = SimpleNamespace(deps=SimpleNamespace(tool_registry=_Registry()))
        return SimpleNamespace(runner=fake_runner)

    async def _fake_run_chat_session(**kwargs):
        captured["capability_pack_metadata"] = kwargs.get("capability_pack_metadata")
        return 0

    monkeypatch.setattr(cli_main, "create_runtime_store_bundle", _fake_store_bundle)
    monkeypatch.setattr(cli_main, "build_cli_provider", lambda _cfg: fake_provider)
    monkeypatch.setattr(
        cli_main, "build_cli_toolset", lambda _cfg: SimpleNamespace(names=())
    )
    monkeypatch.setattr(cli_main, "create_agent", _fake_create_agent)
    monkeypatch.setattr(cli_main, "run_chat_session", _fake_run_chat_session)

    code = cli_main.main(
        [
            "chat",
            "--provider",
            "fake",
            "--plain",
            "--capability-pack-id",
            "deep_research_chat_demo",
            "--capability-adapter-id",
            "chat_demo",
            "--capability-scenario-id",
            "chat_demo.deep_research.source_report.v1",
        ]
    )

    assert code == 0
    assert captured["capability_pack_metadata"] == {
        "capability_pack_id": "deep_research_chat_demo",
        "capability_adapter_id": "chat_demo",
        "capability_scenario_ids": ["chat_demo.deep_research.source_report.v1"],
    }


def test_chat_command_enables_compaction_flags(monkeypatch) -> None:
    """Chat command should create agent with compaction toggles enabled."""
    captured: dict[str, object] = {}

    def _fake_store_bundle(_config):
        return SimpleNamespace(checkpoint_store=object(), event_log=object())

    async def _fake_provider_healthcheck():
        return SimpleNamespace(
            provider_name="fake",
            healthy=True,
            configured=True,
            latency_ms=1.0,
        )

    fake_provider = SimpleNamespace(name="fake", healthcheck=_fake_provider_healthcheck)

    def _fake_create_agent(**kwargs):
        captured["config"] = kwargs.get("config")

        class _Registry:
            @staticmethod
            def list_registered():
                return []

        fake_runner = SimpleNamespace(deps=SimpleNamespace(tool_registry=_Registry()))
        return SimpleNamespace(runner=fake_runner)

    async def _fake_run_chat_session(**_kwargs):
        return 0

    monkeypatch.setattr(cli_main, "create_runtime_store_bundle", _fake_store_bundle)
    monkeypatch.setattr(cli_main, "build_cli_provider", lambda _cfg: fake_provider)
    monkeypatch.setattr(
        cli_main, "build_cli_toolset", lambda _cfg: SimpleNamespace(names=())
    )
    monkeypatch.setattr(cli_main, "create_agent", _fake_create_agent)
    monkeypatch.setattr(cli_main, "run_chat_session", _fake_run_chat_session)

    args = cli_main._build_parser().parse_args(
        ["chat", "--provider", "fake", "--plain"]
    )  # pylint: disable=protected-access
    resolved = cli_main._resolve_args_with_config_and_explicit(  # pylint: disable=protected-access
        args, explicit_options={"--provider", "--plain"}
    )
    code = asyncio.run(cli_main._chat_command(resolved))  # pylint: disable=protected-access
    assert code == 0
    cfg = captured["config"]
    assert cfg is not None
    assert cfg.enable_compaction is True
    assert cfg.enable_session_memory_compaction is True


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
