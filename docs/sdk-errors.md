# SDK Errors

Provider failures are surfaced as typed SDK errors when the runtime failure was
caused by an HTTP provider exception.

```python
from agent_driver.sdk import ProviderStatusError

try:
    output = await agent.query("Hello")
except ProviderStatusError as exc:
    print(exc.status_code)
    print(exc.request_id)
    print(exc.details.response_body)
```

Public error classes:

- `AgentDriverSDKError`
- `ProviderError`
- `ProviderStatusError`
- `ProviderTimeoutError`
- `ProviderTransportError`

`ProviderError.details` contains:

- `provider`
- `status_code`
- `request_id`
- `message`
- `response_body`

Providers that expose request ids through headers such as `x-request-id` also
attach `provider_request_id` to successful LLM response metadata and the
`llm_call_completed` runtime event payload.
