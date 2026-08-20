"""Option A phase-1 regressions: compaction budget correctness + protection parity.

Covers BUG-1 (model-blind 262144 ceiling), BUG-4 (retention dropped evidence-flagged
messages). BUG-2 window fallback is covered in tests/llm/test_context_windows.py.
"""

from types import SimpleNamespace

from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    _is_protected_message,
    _material_unit_receipt,
    _retained_messages_after_full_compaction,
    _scaled_context_char_cap,
)


def _msg(role: str = "user", **metadata):
    return SimpleNamespace(role=role, content="x", metadata=metadata or {})


# --------------------------------------------------------------------------- #
# BUG-4 — the excerpt and post-summary retention share ONE protection predicate.
# --------------------------------------------------------------------------- #


def test_evidence_only_message_is_protected():
    # Previously dropped by _retained_messages_after_full_compaction (data loss).
    assert _is_protected_message(_msg(compaction_evidence=True), is_last=False)


def test_material_unit_hashes_only_message_is_protected():
    assert _is_protected_message(
        _msg(material_unit_hashes=["a1b2"]), is_last=False
    )


def test_plain_message_is_not_protected():
    assert not _is_protected_message(_msg(), is_last=False)


def test_last_and_system_are_protected():
    assert _is_protected_message(_msg(), is_last=True)
    assert _is_protected_message(_msg(role="system"), is_last=False)


def test_retention_keeps_evidence_and_material_hash_messages():
    messages = [
        _msg(role="system"),
        _msg(compaction_evidence=True),
        _msg(material_unit_hashes=["h"]),
        _msg(),  # droppable
        _msg(compaction_protected=True),
        _msg(),  # last -> kept
    ]
    kept = _retained_messages_after_full_compaction(messages)
    # system + evidence + material-hash + protected + last = 5; the bare middle drops.
    assert len(kept) == 5
    assert messages[1] in kept and messages[2] in kept  # the previously-lost ones


def test_receipt_labels_protected_material_hash_as_retained_not_compacted():
    # The second half of BUG-4: because a material-unit-hash message is now retained
    # (shared protection predicate), its hash must be reported under
    # ``retained_unit_hashes`` — never mislabelled as ``compacted``/``omitted``.
    messages = [
        _msg(role="system"),
        _msg(material_unit_hashes=["keep-me"]),
        _msg(),  # a droppable bare turn
        _msg(),  # last -> kept
    ]
    retained = _retained_messages_after_full_compaction(messages)
    receipt = _material_unit_receipt(
        original_messages=messages,
        retained_messages=retained,
        pre_summary_groups_dropped=False,
    )
    assert "keep-me" in receipt["retained_unit_hashes"]
    assert "keep-me" not in receipt["compacted_unit_hashes"]
    assert "keep-me" not in receipt["omitted_unit_hashes"]


def test_receipt_omits_dropped_hashes_when_leading_groups_pre_dropped():
    # A hash that is genuinely unresolved (its message was neither retained nor fed to
    # the summary because leading groups were pre-dropped) is reported as omitted,
    # not falsely credited to the summary — the honest-labelling side of the receipt.
    original = [_msg(material_unit_hashes=["gone"]), _msg(role="system")]
    retained = [_msg(role="system")]  # the hash-bearing message is not retained
    receipt = _material_unit_receipt(
        original_messages=original,
        retained_messages=retained,
        pre_summary_groups_dropped=True,
    )
    assert receipt["omitted_unit_hashes"] == ["gone"]
    assert receipt["compacted_unit_hashes"] == []


# --------------------------------------------------------------------------- #
# BUG-1 — the summariser cap scales with the window, not a fixed 262144.
# --------------------------------------------------------------------------- #


def _host(ptl: int = 4000):
    return SimpleNamespace(_config=SimpleNamespace(ptl_retry_max_chars=ptl))


def test_scaled_cap_exceeds_legacy_262144_on_large_window():
    # A ~200K-token model: max_chars ~720K, generous compaction budget.
    context = SimpleNamespace(
        metadata={
            "effective_context_budget": {
                # A budget whose scaled compaction cap (400k) exceeds the old fixed
                # 262144 ceiling but sits within the window fraction (0.8*720k=576k).
                "max_chars": 720_000,
                "max_compaction_chars": 400_000,
                "source": "model_catalog",
            }
        }
    )
    cap, source = _scaled_context_char_cap(
        _host(), context=context, base_max_chars=4000
    )
    assert source == "model_catalog"
    # Old code clamped to 262144; now it scales toward the window fraction.
    assert cap > 262_144


def test_scaled_cap_bounded_by_window_fraction():
    context = SimpleNamespace(
        metadata={
            "effective_context_budget": {
                "max_chars": 720_000,
                "max_compaction_chars": 10_000_000,  # absurd
                "source": "model_catalog",
            }
        }
    )
    cap, _ = _scaled_context_char_cap(_host(), context=context, base_max_chars=4000)
    # Never exceeds the window's char budget.
    assert cap <= 720_000


def test_scaled_cap_falls_back_without_budget():
    context = SimpleNamespace(metadata={})
    cap, source = _scaled_context_char_cap(
        _host(), context=context, base_max_chars=4000
    )
    assert cap == 4000
    assert source == "runner_config"


def test_scaled_cap_uses_window_not_trim_budget():
    # BUG-5: with a 128k window the compaction cap derives from the window char
    # budget (0.8 * (128000-4000) * 4 = 396800), NOT from the tiny trim max_chars
    # (6000) which would otherwise clamp it. max_compaction_chars is window-derived.
    context = SimpleNamespace(
        metadata={
            "effective_context_budget": {
                "context_window_estimate": 128_000,
                "output_token_reserve": 4_000,
                "max_chars": 6_000,  # the deterministic-trimming budget — must NOT clamp
                "max_compaction_chars": (128_000 - 4_000) * 4,
                "source": "model_catalog",
            }
        }
    )
    cap, _ = _scaled_context_char_cap(_host(), context=context, base_max_chars=4000)
    assert cap > 100_000  # far above the 6000 trim budget and the old 4000
    assert cap == int((128_000 - 4_000) * 4 * 0.8)
