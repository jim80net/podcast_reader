"""Chapter LLM provider registry.

Every provider is an OpenAI-compatible ``/chat/completions`` endpoint described
by data, not code: adding a provider (e.g. a future hosted-inference offering)
is one :data:`PROVIDERS` entry. The ``custom`` entry takes its base URL from
configuration (``EngineSettings.custom_provider_url`` on the engine,
``PODCAST_READER_CUSTOM_PROVIDER_URL`` on the CLI) and accepts only ``https``
URLs or ``http`` URLs on localhost.
"""

from __future__ import annotations

from typing import TypedDict
from urllib.parse import urlparse


class ProviderSpec(TypedDict):
    """One registry entry: where to send requests and how to find the key."""

    base_url: str
    default_model: str
    key_env: str
    max_tokens: int


#: Registry of chapter providers (values verified against provider docs 2026-06-11).
PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        base_url="https://api.anthropic.com/v1",
        default_model="claude-haiku-4-5-20251001",
        key_env="ANTHROPIC_API_KEY",
        max_tokens=16384,
    ),
    "openai": ProviderSpec(
        base_url="https://api.openai.com/v1",
        default_model="gpt-5.4-mini",
        key_env="OPENAI_API_KEY",
        max_tokens=16384,
    ),
    "xai": ProviderSpec(
        base_url="https://api.x.ai/v1",
        default_model="grok-4.3",
        key_env="XAI_API_KEY",
        max_tokens=16384,
    ),
    "openrouter": ProviderSpec(
        base_url="https://openrouter.ai/api/v1",
        default_model="anthropic/claude-haiku-4.5",
        key_env="OPENROUTER_API_KEY",
        max_tokens=16384,
    ),
    "deepseek": ProviderSpec(
        base_url="https://api.deepseek.com",
        default_model="deepseek-v4-flash",
        key_env="DEEPSEEK_API_KEY",
        max_tokens=8192,
    ),
    "custom": ProviderSpec(
        base_url="",  # supplied via resolve_provider(custom_base_url=...)
        default_model="",  # "" means: use the explicitly configured model
        key_env="PODCAST_READER_CUSTOM_PROVIDER_KEY",
        max_tokens=16384,
    ),
}

_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def resolve_provider(name: str, *, custom_base_url: str = "") -> ProviderSpec:
    """Look up *name* in the registry, materializing ``custom`` from config.

    Raises ``ValueError`` for unknown names and for a missing or invalid
    custom base URL.
    """
    try:
        spec = PROVIDERS[name]
    except KeyError:
        raise ValueError(f"Unknown chapter provider: {name!r}") from None
    if name != "custom":
        return spec
    return ProviderSpec(
        base_url=validate_custom_url(custom_base_url),
        default_model=spec["default_model"],
        key_env=spec["key_env"],
        max_tokens=spec["max_tokens"],
    )


def validate_custom_url(url: str) -> str:
    """Validate a custom provider base URL: ``https``, or ``http`` on localhost.

    The localhost trust model accepts that the user configures their own
    machine, but a plain-http remote endpoint would leak the API key.
    """
    if not url:
        raise ValueError(
            "custom provider requires a base URL "
            "(set custom_provider_url / PODCAST_READER_CUSTOM_PROVIDER_URL)"
        )
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and parsed.hostname in _LOCALHOST_HOSTS:
        return url
    raise ValueError("custom provider base URL must be https, or http on localhost/127.0.0.1")
