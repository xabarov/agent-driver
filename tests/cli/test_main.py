"""Tests for packaged product CLI commands."""

from __future__ import annotations

import json

from agent_driver.cli.main import main


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
