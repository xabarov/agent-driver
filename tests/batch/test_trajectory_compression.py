"""N5: trajectory compression to a token budget for training datasets."""

from __future__ import annotations

import pytest

from agent_driver.batch import Trajectory, compress_trajectories, compress_trajectory


def _traj(contents: list[str], *, item_id: str = "i1") -> Trajectory:
    return Trajectory(
        item_id=item_id,
        run_id="r1",
        status="completed",
        answer=contents[-1] if contents else None,
        messages=[
            {"role": "user" if i % 2 == 0 else "assistant", "content": c}
            for i, c in enumerate(contents)
        ],
    )


def test_under_budget_returns_unchanged() -> None:
    traj = _traj(["short", "reply"])
    out = compress_trajectory(traj, max_tokens=1000)
    assert out is traj
    assert "compression" not in out.metadata


def test_over_budget_elides_middle_and_keeps_first_and_last() -> None:
    first, last = "FIRST_TURN " * 5, "LAST_TURN " * 5
    middle = ["MIDDLE " * 100 for _ in range(4)]
    traj = _traj([first, *middle, last])

    out = compress_trajectory(traj, max_tokens=120, keep_first=1, keep_last=1)

    assert out.messages[0]["content"] == first  # first preserved verbatim
    assert out.messages[-1]["content"] == last  # last preserved verbatim
    # One elision marker stands in for the 4 dropped middle turns.
    assert "elided for training budget" in out.messages[1]["content"]
    assert len(out.messages) == 3
    info = out.metadata["compression"]
    assert info["original_message_count"] == 6
    assert info["elided_message_count"] == 4
    assert info["final_tokens"] <= 120 < info["original_tokens"]


def test_keep_first_and_last_counts_respected() -> None:
    msgs = [f"turn-{i} " * 50 for i in range(8)]
    traj = _traj(msgs)
    # Budget holds the 4 preserved turns + marker, but not all 8 → middle elided.
    out = compress_trajectory(traj, max_tokens=450, keep_first=2, keep_last=2)
    assert out.messages[0]["content"] == msgs[0]
    assert out.messages[1]["content"] == msgs[1]
    assert out.messages[-1]["content"] == msgs[7]
    assert out.messages[-2]["content"] == msgs[6]
    assert "elided for training budget" in out.messages[2]["content"]


def test_truncates_preserved_turns_when_they_alone_overflow() -> None:
    """When first+last already exceed budget, their content is truncated to fit."""
    traj = _traj(["A" * 4000, "B" * 4000])  # ~2000 tokens, no middle to elide
    out = compress_trajectory(traj, max_tokens=100, keep_first=1, keep_last=1)
    info = out.metadata["compression"]
    assert info["content_truncated"] is True
    assert info["final_tokens"] <= 100
    assert "truncated" in out.messages[0]["content"]


def test_compress_trajectories_maps_over_list() -> None:
    big = _traj(["X " * 200 for _ in range(6)], item_id="a")
    small = _traj(["hi", "ok"], item_id="b")
    out = compress_trajectories([big, small], max_tokens=80)
    assert "compression" in out[0].metadata
    assert "compression" not in out[1].metadata


def test_invalid_args_raise() -> None:
    traj = _traj(["a", "b"])
    with pytest.raises(ValueError):
        compress_trajectory(traj, max_tokens=0)
    with pytest.raises(ValueError):
        compress_trajectory(traj, max_tokens=10, keep_first=-1)
