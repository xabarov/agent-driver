"""Serve an agent over an OpenAI-compatible HTTP API, offline.

Phase 2 platform adapter: ``agent_driver.server.create_app`` exposes any agent
behind ``POST /v1/chat/completions`` (streaming and non-streaming), ``GET
/v1/models``, and ``GET /healthz`` — so any OpenAI SDK / LibreChat / Open WebUI
client can talk to it. In production you run it via ``agent-driver serve``; here
we drive the same ASGI app in-process with Starlette's ``TestClient`` so the
round-trip runs with no open port and no network.

    python examples/cookbook/17_openai_server.py

Requires the optional dependencies: ``pip install 'agent-driver[server]'``.
"""

from __future__ import annotations

import json

from starlette.testclient import TestClient

from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.server import create_app


def main() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="Hello from the agent-driver server."),
        tools=ToolSet.only(),
    )
    app = create_app(agent, model_id="agent-driver-demo")
    client = TestClient(app)

    # Non-streaming completion.
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "agent-driver-demo",
            "messages": [{"role": "user", "content": "Say hello"}],
        },
    )
    data = resp.json()
    print("non-stream answer ->", data["choices"][0]["message"]["content"])
    print("usage             ->", data["usage"])

    # Streaming completion: collect the chunked deltas.
    print("streaming         ->", end=" ")
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "agent-driver-demo",
            "messages": [{"role": "user", "content": "Say hello"}],
            "stream": True,
        },
    ) as stream:
        for line in stream.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[len("data: ") :])
            piece = chunk["choices"][0]["delta"].get("content", "")
            if piece:
                print(piece, end="")
    print()


if __name__ == "__main__":
    main()
