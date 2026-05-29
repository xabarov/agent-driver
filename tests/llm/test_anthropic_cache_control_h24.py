"""Phase 13 H24 — tests for Anthropic prompt-cache (cache_control ephemeral).

Pins:
  * Default (enable_prompt_cache=False): system stays a string, tools have
    no cache_control — strict backwards-compat with pre-H24 callers.
  * Opt-in (enable_prompt_cache=True): system is rewritten as a content-
    block array with ``cache_control: {"type": "ephemeral"}``; the LAST
    tool in the catalog gets the same marker (Anthropic caches everything
    UP TO and INCLUDING the marker).
  * Earlier tools (non-last) get NO marker — only the outermost marker
    matters; redundant markers waste cache slots.
  * No tools + opt-in: system still cached, no tool-list mutation.
  * No system + opt-in: tools still cached, no system added.
  * Caller's tools list is NOT mutated in place.
"""

from __future__ import annotations

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.anthropic import AnthropicProvider


def _make_provider() -> AnthropicProvider:
    return AnthropicProvider(
        config=AnthropicProvider.Config(api_key="test", model="claude-haiku-4-5")
    )


def _make_request(*, enable_prompt_cache: bool, tools: list[dict] | None = None) -> LlmRequest:
    return LlmRequest(
        messages=[
            ChatMessage(role=ChatRole.SYSTEM, content="You are a recon agent."),
            ChatMessage(role=ChatRole.USER, content="Scan example.org"),
        ],
        tools=tools or [],
        enable_prompt_cache=enable_prompt_cache,
    )


def test_default_keeps_system_as_string_no_cache_markers():
    """Backwards-compat: pre-H24 callers see no behavior change."""
    provider = _make_provider()
    payload = provider._request_payload(
        _make_request(
            enable_prompt_cache=False,
            tools=[
                {"name": "subfinder", "description": "passive subdomain enum", "input_schema": {}},
                {"name": "httpx", "description": "live probe", "input_schema": {}},
            ],
        ),
        stream=False,
    )
    assert payload["system"] == "You are a recon agent."
    for tool in payload["tools"]:
        assert "cache_control" not in tool


def test_opt_in_rewrites_system_as_cache_control_content_block():
    payload = _make_provider()._request_payload(
        _make_request(enable_prompt_cache=True),
        stream=False,
    )
    assert payload["system"] == [
        {
            "type": "text",
            "text": "You are a recon agent.",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_opt_in_marks_only_the_last_tool():
    """Anthropic caches up to & including the marker → ONLY the LAST tool
    gets it; earlier markers would waste cache slots."""
    payload = _make_provider()._request_payload(
        _make_request(
            enable_prompt_cache=True,
            tools=[
                {"name": "subfinder", "description": "x", "input_schema": {}},
                {"name": "httpx", "description": "y", "input_schema": {}},
                {"name": "nuclei", "description": "z", "input_schema": {}},
            ],
        ),
        stream=False,
    )
    assert "cache_control" not in payload["tools"][0]
    assert "cache_control" not in payload["tools"][1]
    assert payload["tools"][2]["cache_control"] == {"type": "ephemeral"}


def test_opt_in_with_no_system_still_caches_tools():
    """System-less request: tools list still gets the marker on last tool."""
    request = LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="ping")],
        tools=[{"name": "t1", "description": "x", "input_schema": {}}],
        enable_prompt_cache=True,
    )
    payload = _make_provider()._request_payload(request, stream=False)
    # No system was provided → not present.
    assert "system" not in payload
    assert payload["tools"][0]["cache_control"] == {"type": "ephemeral"}


def test_opt_in_with_no_tools_still_caches_system():
    """Tool-less request: system still gets the marker, no tool mutation."""
    payload = _make_provider()._request_payload(
        _make_request(enable_prompt_cache=True),
        stream=False,
    )
    assert isinstance(payload["system"], list)
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "tools" not in payload


def test_opt_in_does_not_mutate_callers_tools_list():
    """Caller's list passed by reference must NOT be mutated — provider
    builds the payload via shallow copy on the LAST tool only."""
    original_tools = [
        {"name": "subfinder", "description": "x", "input_schema": {}},
        {"name": "httpx", "description": "y", "input_schema": {}},
    ]
    request = LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="ping")],
        tools=original_tools,
        enable_prompt_cache=True,
    )
    payload = _make_provider()._request_payload(request, stream=False)
    # Payload reflects the marker.
    assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    # Caller's original tools list is unchanged. Pydantic validates +
    # normalizes the list inside the model, so we compare via request.tools
    # which is the model's canonical copy.
    for tool in request.tools:
        assert "cache_control" not in tool


def test_default_field_is_false():
    """enable_prompt_cache defaults to False to keep backwards-compat."""
    request = LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="ping")],
    )
    assert request.enable_prompt_cache is False
