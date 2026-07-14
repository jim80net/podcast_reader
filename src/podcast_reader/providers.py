"""Chapter LLM provider registry.

Every provider is an OpenAI-compatible ``/chat/completions`` endpoint described
by data, not code. :data:`PROVIDERS` holds built-in defaults; user-defined named
entries are validated and merged into fresh effective registries without
mutating those defaults. The legacy ``custom`` entry takes its base URL from
configuration (``EngineSettings.custom_provider_url`` on the engine,
``PODCAST_READER_CUSTOM_PROVIDER_URL`` on the CLI). All user-controlled URLs
accept only credential-free ``https`` URLs or ``http`` URLs on localhost.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TypedDict, cast
from urllib.parse import urlparse

from podcast_reader.types import CustomProviderConfig


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
_CUSTOM_PROVIDER_FIELDS = frozenset({"name", "base_url", "default_model", "max_tokens"})
_PROVIDER_NAME_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_MAX_CUSTOM_PROVIDERS = 100


def provider_key_env(name: str) -> str:
    """Return the collision-free environment key name for a valid custom name."""
    if _PROVIDER_NAME_RE.fullmatch(name) is None or len(name) > 63:
        raise ValueError(f"invalid custom provider name: {name!r}")
    return f"PODCAST_READER_PROVIDER_{name.upper().replace('-', '_')}_KEY"


def validate_custom_provider(value: Mapping[str, object]) -> CustomProviderConfig:
    """Validate and return a fresh canonical nonsecret provider configuration."""
    extras = set(value) - _CUSTOM_PROVIDER_FIELDS
    missing = _CUSTOM_PROVIDER_FIELDS - set(value)
    if extras:
        raise ValueError(f"custom provider has unknown fields: {sorted(extras)!r}")
    if missing:
        raise ValueError(f"custom provider is missing fields: {sorted(missing)!r}")

    name_value = value["name"]
    if not isinstance(name_value, str):
        raise ValueError("custom provider name must be a string")
    name = name_value.strip()
    if len(name) > 63 or _PROVIDER_NAME_RE.fullmatch(name) is None:
        raise ValueError("custom provider name must be a lowercase slug of at most 63 characters")
    if name in PROVIDERS:
        raise ValueError(f"custom provider name {name!r} is reserved")

    base_url_value = value["base_url"]
    if not isinstance(base_url_value, str):
        raise ValueError(f"custom provider {name!r} base_url must be a string")
    base_url = validate_custom_url(base_url_value.strip())

    model_value = value["default_model"]
    if not isinstance(model_value, str):
        raise ValueError(f"custom provider {name!r} default_model must be a string")
    default_model = model_value.strip()
    if not default_model or len(default_model) > 256:
        raise ValueError(f"custom provider {name!r} default_model must be 1 to 256 characters")

    max_tokens = value["max_tokens"]
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise ValueError(f"custom provider {name!r} max_tokens must be an integer")
    if not 1 <= max_tokens <= 1_000_000:
        raise ValueError(f"custom provider {name!r} max_tokens must be 1 to 1000000")

    return CustomProviderConfig(
        name=name,
        base_url=base_url,
        default_model=default_model,
        max_tokens=max_tokens,
    )


def canonicalize_custom_providers(value: object) -> list[CustomProviderConfig]:
    """Validate a settings value and return fresh entries in its original order."""
    if not isinstance(value, list):
        raise ValueError("custom_providers must be a list")
    if len(value) > _MAX_CUSTOM_PROVIDERS:
        raise ValueError("custom_providers may contain at most 100 entries")
    result: list[CustomProviderConfig] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError(f"custom provider entry {index} must be an object")
        config = validate_custom_provider(cast("Mapping[str, object]", raw))
        if config["name"] in seen:
            raise ValueError(f"duplicate custom provider name: {config['name']!r}")
        seen.add(config["name"])
        result.append(config)
    return result


def build_provider_registry(
    custom_providers: Sequence[Mapping[str, object]] = (),
) -> dict[str, ProviderSpec]:
    """Build a fresh effective registry from built-ins plus validated entries."""
    registry = {name: ProviderSpec(**spec) for name, spec in PROVIDERS.items()}
    canonical = canonicalize_custom_providers(list(custom_providers))
    for config in canonical:
        registry[config["name"]] = ProviderSpec(
            base_url=config["base_url"],
            default_model=config["default_model"],
            key_env=provider_key_env(config["name"]),
            max_tokens=config["max_tokens"],
        )
    return registry


def resolve_provider(
    name: str,
    *,
    custom_base_url: str = "",
    custom_providers: Sequence[Mapping[str, object]] = (),
) -> ProviderSpec:
    """Look up *name* in the registry, materializing ``custom`` from config.

    Raises ``ValueError`` for unknown names and for a missing or invalid
    custom base URL.
    """
    try:
        spec = build_provider_registry(custom_providers)[name]
    except KeyError:
        raise ValueError(f"Unknown chapter provider: {name!r}") from None
    if name != "custom":
        return ProviderSpec(**spec)
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
    if not parsed.hostname:
        raise ValueError("custom provider base URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("custom provider base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("custom provider base URL must not contain a query or fragment")
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and parsed.hostname in _LOCALHOST_HOSTS:
        return url
    raise ValueError("custom provider base URL must be https, or http on localhost/127.0.0.1")
