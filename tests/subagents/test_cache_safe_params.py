"""Phase 12 H19 — tests for cache-safe params sharing across subagents.

Pins:
* identical parent prefix + tools + model → identical cache_key;
* different model → different cache_key (provider keys by model);
* different tools list → different signature → different cache_key;
* tool order doesn't affect signature (registration order
  independence);
* apply_to_child_run_input attaches the metadata under the
  canonical key without mutating other fields;
* CacheSafeParams round-trips through ``to_metadata`` /
  ``from_metadata``;
* provider_cache_hint_for emits the right shape per provider;
* unknown provider kind returns a safe no-op hint.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
)
from agent_driver.subagents.cache_safe_params import (
    CACHE_SAFE_METADATA_KEY,
    CacheSafeParams,
    apply_to_child_run_input,
    compute_cache_safe_params,
    provider_cache_hint_for,
)


def _run_input(*, input_text: str = "hello", run_id: str = "r1") -> AgentRunInput:
    return AgentRunInput(
        input=input_text,
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


def _tool(name: str, description: str = "x") -> ToolManifest:
    return ToolManifest(name=name, description=description)


# -- cache_key stability ---------------------------------------------------


def test_identical_inputs_produce_identical_cache_key():
    run = _run_input()
    tools = [_tool("a"), _tool("b")]
    p1 = compute_cache_safe_params(
        run_input=run, system_prompt="sys", tools=tools, model="claude-haiku-4-5"
    )
    p2 = compute_cache_safe_params(
        run_input=run, system_prompt="sys", tools=tools, model="claude-haiku-4-5"
    )
    assert p1.cache_key == p2.cache_key
    assert p1 == p2


def test_different_model_produces_different_cache_key():
    run = _run_input()
    tools = [_tool("a")]
    p_haiku = compute_cache_safe_params(
        run_input=run, system_prompt="sys", tools=tools, model="claude-haiku-4-5"
    )
    p_opus = compute_cache_safe_params(
        run_input=run, system_prompt="sys", tools=tools, model="claude-opus-4-7"
    )
    assert p_haiku.cache_key != p_opus.cache_key


def test_different_tools_produces_different_signature():
    run = _run_input()
    p_one = compute_cache_safe_params(
        run_input=run, system_prompt=None, tools=[_tool("a")], model="m"
    )
    p_two = compute_cache_safe_params(
        run_input=run, system_prompt=None, tools=[_tool("a"), _tool("b")], model="m"
    )
    assert p_one.tools_signature != p_two.tools_signature
    assert p_one.cache_key != p_two.cache_key


def test_tool_order_does_not_affect_signature():
    """Registration order shouldn't break cache sharing."""
    run = _run_input()
    p_ab = compute_cache_safe_params(
        run_input=run,
        system_prompt=None,
        tools=[_tool("a"), _tool("b"), _tool("c")],
        model="m",
    )
    p_cba = compute_cache_safe_params(
        run_input=run,
        system_prompt=None,
        tools=[_tool("c"), _tool("b"), _tool("a")],
        model="m",
    )
    assert p_ab.tools_signature == p_cba.tools_signature
    assert p_ab.cache_key == p_cba.cache_key


def test_different_input_produces_different_prefix_hash():
    """Parent prefix differs → cache cannot be shared."""
    run_a = _run_input(input_text="task A")
    run_b = _run_input(input_text="task B")
    p_a = compute_cache_safe_params(run_input=run_a, model="m")
    p_b = compute_cache_safe_params(run_input=run_b, model="m")
    assert p_a.parent_prefix_hash != p_b.parent_prefix_hash
    assert p_a.cache_key != p_b.cache_key


def test_different_system_prompt_produces_different_hash():
    run = _run_input()
    p_one = compute_cache_safe_params(
        run_input=run, system_prompt="prompt A", model="m"
    )
    p_two = compute_cache_safe_params(
        run_input=run, system_prompt="prompt B", model="m"
    )
    assert p_one.system_prompt_hash != p_two.system_prompt_hash


def test_empty_system_prompt_produces_empty_hash():
    run = _run_input()
    p_none = compute_cache_safe_params(run_input=run, system_prompt=None, model="m")
    p_empty = compute_cache_safe_params(run_input=run, system_prompt="", model="m")
    assert p_none.system_prompt_hash == ""
    assert p_empty.system_prompt_hash == ""
    assert p_none.cache_key == p_empty.cache_key


# -- apply_to_child_run_input ---------------------------------------------


def test_apply_attaches_metadata_under_canonical_key():
    run = _run_input(input_text="child task")
    params = compute_cache_safe_params(run_input=run, model="m")
    updated = apply_to_child_run_input(run, params)
    assert CACHE_SAFE_METADATA_KEY in updated.app_metadata
    stored = updated.app_metadata[CACHE_SAFE_METADATA_KEY]
    assert stored["cache_key"] == params.cache_key
    assert stored["model"] == "m"


def test_apply_preserves_existing_metadata():
    run = AgentRunInput(
        input="x",
        run_id="r1",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        app_metadata={"unrelated": "preserved"},
    )
    params = compute_cache_safe_params(run_input=run, model="m")
    updated = apply_to_child_run_input(run, params)
    assert updated.app_metadata.get("unrelated") == "preserved"
    assert updated.app_metadata.get(CACHE_SAFE_METADATA_KEY)["cache_key"] == params.cache_key


def test_apply_does_not_mutate_original():
    run = _run_input()
    params = compute_cache_safe_params(run_input=run, model="m")
    _ = apply_to_child_run_input(run, params)
    # Original unchanged.
    assert CACHE_SAFE_METADATA_KEY not in run.app_metadata


# -- metadata round-trip --------------------------------------------------


def test_params_round_trip_through_metadata():
    run = _run_input()
    original = compute_cache_safe_params(
        run_input=run, system_prompt="sys", tools=[_tool("t")], model="m"
    )
    raw = original.to_metadata()
    restored = CacheSafeParams.from_metadata(raw)
    assert restored == original


def test_params_from_metadata_with_missing_fields_coerces_to_empty():
    """A malformed app_metadata entry shouldn't crash; resulting
    cache_key just won't match a real CacheSafeParams."""
    restored = CacheSafeParams.from_metadata({})
    assert restored.system_prompt_hash == ""
    assert restored.tools_signature == ""
    assert restored.model == ""
    assert restored.parent_prefix_hash == ""
    assert restored.cache_key == ""


# -- provider_cache_hint_for -----------------------------------------------


def test_provider_hint_anthropic_includes_cache_control_target():
    params = compute_cache_safe_params(run_input=_run_input(), model="claude-opus-4-7")
    hint = provider_cache_hint_for(params=params, provider_kind="anthropic")
    assert hint.kind == "anthropic"
    assert hint.request_overrides["_cache_control_target"] == "system_prompt"
    assert hint.message_cache_breakpoint == 0
    assert hint.request_overrides["_cache_safe_params"]["cache_key"] == params.cache_key


def test_provider_hint_openai_uses_extra_body():
    params = compute_cache_safe_params(run_input=_run_input(), model="gpt-4o")
    hint = provider_cache_hint_for(params=params, provider_kind="openai_compatible")
    assert hint.kind == "openai_compatible"
    assert "extra_body" in hint.request_overrides
    assert hint.request_overrides["extra_body"]["_cache_safe_params"]["cache_key"] == params.cache_key
    assert hint.message_cache_breakpoint is None


def test_provider_hint_vllm_uses_extra_body_like_openai():
    params = compute_cache_safe_params(run_input=_run_input(), model="qwen3")
    hint = provider_cache_hint_for(params=params, provider_kind="vllm")
    assert hint.kind == "vllm"
    assert "extra_body" in hint.request_overrides


def test_provider_hint_ollama_no_override():
    """Ollama doesn't support portable prompt caching; emit no
    request override but still surface params for observability."""
    params = compute_cache_safe_params(run_input=_run_input(), model="llama")
    hint = provider_cache_hint_for(params=params, provider_kind="ollama")
    assert hint.kind == "ollama"
    assert hint.request_overrides == {}


def test_provider_hint_unknown_kind_safe_noop():
    params = compute_cache_safe_params(run_input=_run_input(), model="m")
    hint = provider_cache_hint_for(params=params, provider_kind="bespoke_provider")
    assert hint.kind == "unknown"
    assert hint.request_overrides == {}


def test_provider_hint_case_insensitive_kind():
    params = compute_cache_safe_params(run_input=_run_input(), model="m")
    h_upper = provider_cache_hint_for(params=params, provider_kind="ANTHROPIC")
    h_mixed = provider_cache_hint_for(params=params, provider_kind="Anthropic")
    assert h_upper.kind == "anthropic"
    assert h_mixed.kind == "anthropic"
