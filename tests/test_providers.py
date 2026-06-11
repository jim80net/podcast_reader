"""Tests for the chapter LLM provider registry (podcast_reader.providers)."""

from __future__ import annotations

import pytest

from podcast_reader.providers import (
    PROVIDERS,
    ProviderSpec,
    resolve_provider,
    validate_custom_url,
)


class TestRegistry:
    """Spec: Provider registry — six entries with verified data."""

    @pytest.mark.parametrize(
        "name", ["anthropic", "openai", "xai", "openrouter", "deepseek", "custom"]
    )
    def test_known_providers_resolvable(self, name: str) -> None:
        """Every provider name resolves to a complete spec."""
        spec = PROVIDERS[name]
        assert set(spec) == {"base_url", "default_model", "key_env", "max_tokens"}

    def test_registry_values_match_design_table(self) -> None:
        assert PROVIDERS["anthropic"] == ProviderSpec(
            base_url="https://api.anthropic.com/v1",
            default_model="claude-haiku-4-5-20251001",
            key_env="ANTHROPIC_API_KEY",
            max_tokens=16384,
        )
        assert PROVIDERS["openai"] == ProviderSpec(
            base_url="https://api.openai.com/v1",
            default_model="gpt-5.4-mini",
            key_env="OPENAI_API_KEY",
            max_tokens=16384,
        )
        assert PROVIDERS["xai"] == ProviderSpec(
            base_url="https://api.x.ai/v1",
            default_model="grok-4.3",
            key_env="XAI_API_KEY",
            max_tokens=16384,
        )
        assert PROVIDERS["openrouter"] == ProviderSpec(
            base_url="https://openrouter.ai/api/v1",
            default_model="anthropic/claude-haiku-4.5",
            key_env="OPENROUTER_API_KEY",
            max_tokens=16384,
        )
        assert PROVIDERS["deepseek"] == ProviderSpec(
            base_url="https://api.deepseek.com",
            default_model="deepseek-v4-flash",
            key_env="DEEPSEEK_API_KEY",
            max_tokens=8192,
        )
        assert PROVIDERS["custom"]["key_env"] == "PODCAST_READER_CUSTOM_PROVIDER_KEY"
        assert PROVIDERS["custom"]["max_tokens"] == 16384


class TestResolveProvider:
    def test_named_provider_returned_verbatim(self) -> None:
        assert resolve_provider("deepseek") == PROVIDERS["deepseek"]

    def test_unknown_provider_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown chapter provider"):
            resolve_provider("not-a-provider")

    def test_custom_provider_takes_base_url_from_config(self) -> None:
        spec = resolve_provider("custom", custom_base_url="https://llm.example.com/v1")
        assert spec["base_url"] == "https://llm.example.com/v1"
        assert spec["key_env"] == "PODCAST_READER_CUSTOM_PROVIDER_KEY"
        assert spec["max_tokens"] == 16384

    def test_custom_provider_without_url_rejected(self) -> None:
        with pytest.raises(ValueError, match="base URL"):
            resolve_provider("custom")


class TestCustomUrlValidation:
    """Spec: Custom URL validation — https, or http on localhost only."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://llm.example.com/v1",
            "http://127.0.0.1:8080/v1",
            "http://localhost:11434/v1",
            "http://[::1]:8080/v1",
        ],
    )
    def test_accepts_https_and_localhost_http(self, url: str) -> None:
        assert validate_custom_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "http://evil.example.com",
            "http://192.168.1.10:8080/v1",
            "ftp://example.com",
            "not-a-url",
            "",
        ],
    )
    def test_rejects_everything_else(self, url: str) -> None:
        with pytest.raises(ValueError, match="custom provider"):
            validate_custom_url(url)
