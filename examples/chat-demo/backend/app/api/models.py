"""OpenRouter models proxy for chat demo UI."""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from app.deps import get_settings
from app.schemas.meta import ModelView, ModelsResponse

router = APIRouter(tags=["meta"])

_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_CACHE_TTL_SECONDS = 600.0


def _parse_models_payload(raw: dict[str, Any]) -> list[ModelView]:
    data = raw.get("data")
    if not isinstance(data, list):
        return []
    models: list[ModelView] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        context = item.get("context_length")
        models.append(
            ModelView(
                id=model_id,
                name=item.get("name") if isinstance(item.get("name"), str) else None,
                description=item.get("description")
                if isinstance(item.get("description"), str)
                else None,
                context_length=context if isinstance(context, int) else None,
            )
        )
    models.sort(key=lambda entry: entry.id)
    return models


@router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    """Proxy OpenRouter model catalog for the model picker."""
    settings = get_settings()
    if settings.provider != "openrouter":
        default_model = settings.model or "default"
        return ModelsResponse(
            provider=settings.provider,
            models=[ModelView(id=default_model, name=default_model)],
        )

    api_key = settings.api_key
    if not api_key:
        raise HTTPException(status_code=400, detail="AGENT_DRIVER_API_KEY is required for models")

    now = time.monotonic()
    cached = _CACHE.get("payload")
    if cached is not None and now < float(_CACHE.get("expires_at", 0.0)):
        return cached

    base_url = (settings.base_url or "https://openrouter.ai/api/v1").rstrip("/")
    url = f"{base_url}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"failed to fetch models: {exc}") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="invalid models response")

    result = ModelsResponse(provider="openrouter", models=_parse_models_payload(payload))
    _CACHE["payload"] = result
    _CACHE["expires_at"] = now + _CACHE_TTL_SECONDS
    return result
