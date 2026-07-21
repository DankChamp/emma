"""
Settings / Providers API.

This is what the GUI's "Providers & API Keys" screen talks to. It lets
the user, from a form instead of hand-editing .env:
  - see every provider Emma knows about, whether it's configured, and
    whether it's reachable right now
  - see (or fetch, for Ollama) which models are available on it
  - save an API key or default model, which is written straight into
    .env so it survives a restart
  - run a quick live test of a provider

Nothing here is business logic - it's a thin, read/write layer over
config.update_env_file() and the AIRouter's provider registry.
"""
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_ai_router
from config import Settings, get_settings, update_env_file
from core.router import AIRouter

router = APIRouter(prefix="/settings", tags=["settings"])

# Maps each provider name to the .env variable(s) that control it.
# "api_key": None means the provider doesn't use a key (e.g. local Ollama).
PROVIDER_ENV_KEYS: dict[str, dict[str, Optional[str]]] = {
    "groq": {"api_key": "GROQ_API_KEY", "model": "GROQ_DEFAULT_MODEL"},
    "nvidia_nim": {"api_key": "NVIDIA_NIM_API_KEY", "model": "NVIDIA_NIM_DEFAULT_MODEL"},
    "ollama": {"api_key": None, "model": "OLLAMA_DEFAULT_MODEL"},
    "local_generic": {"api_key": "LOCAL_API_KEY", "model": "LOCAL_DEFAULT_MODEL"},
}

# Providers with a user-editable base URL (as opposed to a fixed cloud
# endpoint). Maps provider name -> its .env variable.
PROVIDER_BASE_URL_KEYS: dict[str, str] = {
    "ollama": "OLLAMA_BASE_URL",
    "local_generic": "LOCAL_BASE_URL",
}

# Cloud providers' model listing endpoints (OpenAI-compatible /v1/models).
# When an API key is configured, Emma fetches live models instead of
# relying on the curated list below. The curated list is still used as a
# fallback when the fetch fails or the key hasn't been set yet.
CLOUD_MODEL_ENDPOINTS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1/models",
    "nvidia_nim": "https://integrate.api.nvidia.com/v1/models",
}

# Curated suggestions shown in the GUI's model dropdown for cloud providers.
# These are editable/typeable in the GUI too - Emma can't know every model
# a given key has access to, so this is a starting point, not a hard list.
SUGGESTED_MODELS: dict[str, list[str]] = {
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
        "deepseek-r1-distill-llama-70b",
    ],
    "nvidia_nim": [
        "deepseek-ai/deepseek-v4-flash",
        "meta/llama-3.1-405b-instruct",
        "nvidia/nemotron-4-340b-instruct",
        "mistralai/mixtral-8x22b-instruct-v0.1",
    ],
}


class ProviderKeyUpdate(BaseModel):
    api_key: str


class ProviderModelUpdate(BaseModel):
    model: str


class OllamaUrlUpdate(BaseModel):
    base_url: str


@router.get("/providers")
async def list_providers(ai_router: AIRouter = Depends(get_ai_router)):
    """Live status for every provider - used to populate the Providers screen."""
    return await ai_router.provider_status()


@router.get("/providers/{provider}/models")
async def provider_models(provider: str, settings: Settings = Depends(get_settings)):
    """
    Model choices for a provider's dropdown. For Ollama this is a live
    lookup of whatever's actually pulled locally; for cloud providers it's
    the curated suggestion list (the field stays editable either way).
    """
    if provider not in PROVIDER_ENV_KEYS:
        raise HTTPException(404, f"Unknown provider '{provider}'")

    if provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
                resp.raise_for_status()
                data = resp.json()
            return {"models": [m["name"] for m in data.get("models", [])]}
        except httpx.HTTPError:
            return {"models": [], "error": "Could not reach Ollama - is it running?"}

    if provider == "local_generic":
        if not settings.local_base_url:
            return {"models": [], "error": "Set a base URL for the local provider first."}
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    f"{settings.local_base_url.rstrip('/')}/v1/models",
                    headers={"Authorization": f"Bearer {settings.local_api_key or 'not-needed'}"},
                )
                resp.raise_for_status()
                data = resp.json()
            return {"models": [m["id"] for m in data.get("data", [])]}
        except httpx.HTTPError:
            return {"models": [], "error": "Could not reach that server - is it running?"}

    # For cloud providers (groq, nvidia_nim), try to fetch live models from
    # their /v1/models endpoint using the configured API key. Falls back to
    # the curated suggestion list if the endpoint is unreachable.
    api_key = _provider_api_key(provider, settings)
    endpoint = CLOUD_MODEL_ENDPOINTS.get(provider)
    if api_key and endpoint:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    endpoint,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
            models = [m["id"] for m in data.get("data", [])]
            if models:
                return {"models": sorted(models)}
        except httpx.HTTPError:
            pass  # fall through to curated list

    return {"models": SUGGESTED_MODELS.get(provider, [])}


@router.post("/providers/{provider}/key")
def set_provider_key(provider: str, payload: ProviderKeyUpdate):
    entry = PROVIDER_ENV_KEYS.get(provider)
    if entry is None:
        raise HTTPException(404, f"Unknown provider '{provider}'")
    if entry["api_key"] is None:
        raise HTTPException(400, f"Provider '{provider}' doesn't use an API key")

    update_env_file({entry["api_key"]: payload.api_key.strip()})
    return {"ok": True}


@router.post("/providers/{provider}/model")
def set_provider_model(provider: str, payload: ProviderModelUpdate):
    entry = PROVIDER_ENV_KEYS.get(provider)
    if entry is None:
        raise HTTPException(404, f"Unknown provider '{provider}'")

    update_env_file({entry["model"]: payload.model.strip()})
    return {"ok": True}


@router.post("/ollama/base-url")
def set_ollama_base_url(payload: OllamaUrlUpdate):
    update_env_file({"OLLAMA_BASE_URL": payload.base_url.strip()})
    return {"ok": True}


@router.post("/providers/{provider}/base-url")
def set_provider_base_url(provider: str, payload: OllamaUrlUpdate):
    """
    Generic version of the endpoint above - works for any provider with a
    user-editable base URL (currently Ollama and the generic local
    provider). Kept alongside /ollama/base-url rather than replacing it, so
    nothing that already calls the old route breaks.
    """
    env_key = PROVIDER_BASE_URL_KEYS.get(provider)
    if env_key is None:
        raise HTTPException(404, f"Provider '{provider}' doesn't have a configurable base URL")

    update_env_file({env_key: payload.base_url.strip()})
    return {"ok": True}


def _provider_api_key(provider: str, settings: Settings) -> Optional[str]:
    """Return the configured API key for a given cloud provider, or None."""
    mapping = {
        "groq": settings.groq_api_key,
        "nvidia_nim": settings.nvidia_nim_api_key,
        "local_generic": settings.local_api_key,
    }
    return mapping.get(provider)


@router.post("/providers/{provider}/test")
async def test_provider(provider: str, ai_router: AIRouter = Depends(get_ai_router)):
    """Quick reachability check - powers the 'Test' button next to each provider."""
    prov = ai_router.providers_by_name.get(provider)
    if prov is None:
        raise HTTPException(404, f"Unknown provider '{provider}'")
    available = await prov.is_available()
    return {"provider": provider, "available": available}
