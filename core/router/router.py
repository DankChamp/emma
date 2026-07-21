"""
The AI Router.

This is the piece that lets Emma "decide" instead of the user having to
pick a model every time. It maps a TaskType to an ordered preference list
of providers, then walks that list until one is actually available.

Adding a new provider or task type never requires touching call sites -
only this file's routing table changes.
"""
import logging
from enum import Enum
from typing import Optional

import httpx

from config import Settings

logger = logging.getLogger("emma.router")
from .providers import (
    AIProvider,
    CompletionResult,
    GroqProvider,
    OllamaProvider,
    LocalGenericProvider,
    NvidiaNIMProvider,
)


class TaskType(str, Enum):
    CONVERSATION = "conversation"      # quick chit-chat, small talk
    CODING = "coding"                  # writing/debugging code
    REASONING = "reasoning"            # long, multi-step reasoning
    CREATIVE = "creative"              # creative writing
    GENERAL_ASSISTANT = "general"      # fast general tasks, summaries, routing decisions


class AIRouter:
    """
    Usage:
        router = AIRouter(settings)
        result = await router.run(TaskType.CODING, "Write a function that...")
    """

    def __init__(self, settings: Settings):
        self.settings = settings

        self._ollama = OllamaProvider(settings.ollama_base_url, settings.ollama_default_model)
        self._local = LocalGenericProvider(
            settings.local_base_url, settings.local_default_model, settings.local_api_key
        )
        self._groq = GroqProvider(settings.groq_api_key, settings.groq_default_model)
        self._nim = NvidiaNIMProvider(settings.nvidia_nim_api_key, settings.nvidia_nim_default_model)

        # Both "local" providers - Ollama and the generic OpenAI-compatible
        # one - are equally "local" for routing-preference purposes.
        self._local_providers = [self._ollama, self._local]

        # Routing table: task type -> ordered provider preference.
        # This is the thing to edit as you add providers or change your mind
        # about what's "best" for a given task.
        self._routing_table: dict[TaskType, list[AIProvider]] = {
            TaskType.CONVERSATION: [self._ollama, self._local, self._groq],
            TaskType.GENERAL_ASSISTANT: [self._groq, self._ollama, self._local],
            TaskType.CODING: [self._nim, self._groq, self._ollama, self._local],
            TaskType.REASONING: [self._nim, self._groq, self._ollama, self._local],
            TaskType.CREATIVE: [self._groq, self._ollama, self._local],
        }

        if settings.prefer_local_when_available:
            # Push the local providers to the front of every list, in their
            # existing relative order, ahead of any cloud provider.
            for task, providers in self._routing_table.items():
                locals_present = [p for p in self._local_providers if p in providers]
                others = [p for p in providers if p not in self._local_providers]
                providers[:] = locals_present + others

        # Safety net: guarantee every task can reach every provider. Each task
        # keeps its own preference order, but any provider not already listed is
        # appended as a last resort. Without this, a task whose preferred
        # providers are all down (e.g. "conversation" prefers Ollama/Groq) would
        # fail even when another provider - NVIDIA NIM, say - is up and idle.
        global_fallback = [self._ollama, self._local, self._groq, self._nim]
        for task, providers in self._routing_table.items():
            for provider in global_fallback:
                if provider not in providers:
                    providers.append(provider)

        # Lookup by name - used for "Manual mode" (GUI lets the user force a
        # specific provider/model instead of letting Emma choose) and for the
        # settings screen that reports live status per provider.
        self.providers_by_name: dict[str, AIProvider] = {
            self._ollama.name: self._ollama,
            self._local.name: self._local,
            self._groq.name: self._groq,
            self._nim.name: self._nim,
        }

    async def provider_status(self) -> list[dict]:
        """
        Live status for every known provider - whether it has credentials
        configured, what model it'll use by default, and whether it's
        actually reachable right now. This is what powers the GUI's
        Providers / API Keys screen.
        """
        status = []
        for name, provider in self.providers_by_name.items():
            if provider is self._local:
                # No API key needed for most local servers - "configured"
                # here means "has a base URL to talk to" instead.
                configured = bool(getattr(provider, "base_url", None))
            else:
                configured = bool(getattr(provider, "api_key", True))
            available = await provider.is_available()
            status.append(
                {
                    "name": name,
                    "configured": configured,
                    "default_model": getattr(provider, "default_model", None),
                    "available": available,
                }
            )
        return status

    def _candidates(self, task: TaskType) -> list[AIProvider]:
        return self._routing_table.get(task, [self._ollama, self._groq, self._nim])

    async def run(
        self,
        task: TaskType,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
        provider_name: Optional[str] = None,
    ) -> CompletionResult:
        """
        Run a completion, resiliently.

        provider_name, when given, forces a specific provider ("Manual mode"
        in the GUI) instead of letting the task-type routing table decide.

        In automatic mode Emma walks the task's provider preference list and
        tries each *available* provider in turn. Crucially, if a provider is
        reachable but the actual request fails - a bad/expired API key (403),
        a model that doesn't exist (404), a rate limit (429), a timeout, a
        server hiccup (5xx) - she doesn't give up: she falls back to the next
        provider. Only if every candidate fails does she raise, and then with
        a single human-readable message instead of a raw stack trace.
        """
        if provider_name:
            provider = self.providers_by_name.get(provider_name)
            if provider is None:
                raise RuntimeError(f"Unknown provider '{provider_name}'.")
            kwargs = {"model": model} if model else {}
            try:
                return await provider.complete(prompt, system=system, **kwargs)
            except Exception as exc:  # noqa: BLE001 - surface a clean message
                raise RuntimeError(
                    f"Provider '{provider_name}' couldn't answer: {self._describe_error(exc)}"
                ) from exc

        candidates = self._candidates(task)
        errors: list[str] = []
        tried_any = False

        for provider in candidates:
            try:
                if not await provider.is_available():
                    continue
            except Exception as exc:  # noqa: BLE001 - availability probe blew up
                errors.append(f"{provider.name}: {self._describe_error(exc)}")
                continue

            tried_any = True
            kwargs = {"model": model} if model else {}
            try:
                result = await provider.complete(prompt, system=system, **kwargs)
                if not (result.text and result.text.strip()):
                    # Some endpoints return 200 with an empty body when a model
                    # is overloaded. Treat that as a failure and fall back.
                    raise RuntimeError("provider returned an empty response")
                if errors:
                    logger.info(
                        "Emma fell back to %s after: %s", provider.name, "; ".join(errors)
                    )
                return result
            except Exception as exc:  # noqa: BLE001 - try the next provider
                msg = self._describe_error(exc)
                logger.warning("Provider %s failed (%s); trying next.", provider.name, msg)
                errors.append(f"{provider.name}: {msg}")
                continue

        if not tried_any and not errors:
            raise RuntimeError(
                f"No AI provider is available for '{task.value}'. Start Ollama, "
                "point Emma at a local server, or add a working cloud API key "
                "on the Providers screen."
            )

        detail = "; ".join(errors) if errors else "all providers unavailable"
        raise RuntimeError(
            f"Emma tried every provider for '{task.value}' but none could answer "
            f"({detail}). Check your API keys and models on the Providers screen."
        )

    @staticmethod
    def _describe_error(exc: Exception) -> str:
        """Turn a provider exception into a short, user-facing explanation."""
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            reasons = {
                401: "invalid or missing API key",
                403: "API key rejected (403 - check the key is valid)",
                404: "model not found (check the default model name)",
                429: "rate limited - too many requests",
            }
            if code in reasons:
                return reasons[code]
            if code >= 500:
                return f"provider server error ({code})"
            return f"HTTP {code}"
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return "could not connect (is the server running / online?)"
        if isinstance(exc, httpx.TimeoutException):
            return "timed out"
        return str(exc) or exc.__class__.__name__
