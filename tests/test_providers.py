"""Tests for the chapter LLM provider registry (podcast_reader.providers)."""

from __future__ import annotations

import pytest

from podcast_reader.providers import (
    PROVIDERS,
    CustomProviderConfig,
    ProviderSpec,
    build_provider_registry,
    provider_key_env,
    resolve_provider,
    validate_custom_provider,
    validate_custom_url,
)

OFFICE_PROVIDER = CustomProviderConfig(
    name="office-gateway",
    base_url="https://llm.corp.example/v1",
    default_model="corp-small",
    max_tokens=32768,
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

    def test_user_defined_provider_resolves_with_per_name_key_env(self) -> None:
        spec = resolve_provider("office-gateway", custom_providers=[OFFICE_PROVIDER])

        assert spec == ProviderSpec(
            base_url="https://llm.corp.example/v1",
            default_model="corp-small",
            key_env="PODCAST_READER_PROVIDER_OFFICE_GATEWAY_KEY",
            max_tokens=32768,
        )


class TestUserDefinedProviders:
    def test_key_env_mapping_is_collision_free_for_valid_names(self) -> None:
        assert provider_key_env("opencode-zen") == "PODCAST_READER_PROVIDER_OPENCODE_ZEN_KEY"
        assert provider_key_env("opencodezen") == "PODCAST_READER_PROVIDER_OPENCODEZEN_KEY"

    def test_registry_is_builtins_then_settings_order(self) -> None:
        second = CustomProviderConfig(
            name="local-llama",
            base_url="http://127.0.0.1:11434/v1",
            default_model="llama-4",
            max_tokens=8192,
        )

        registry = build_provider_registry([OFFICE_PROVIDER, second])

        assert list(registry) == [*PROVIDERS, "office-gateway", "local-llama"]

    def test_registry_specs_and_inputs_are_defensive_copies(self) -> None:
        original = dict(OFFICE_PROVIDER)
        configs = [original]
        registry = build_provider_registry(configs)
        registry["anthropic"]["default_model"] = "mutated"
        registry["office-gateway"]["default_model"] = "mutated"
        original["default_model"] = "also-mutated"

        fresh = build_provider_registry([OFFICE_PROVIDER])

        assert PROVIDERS["anthropic"]["default_model"] != "mutated"
        assert fresh["office-gateway"]["default_model"] == "corp-small"

    @pytest.mark.parametrize(
        "name",
        ["OpenCode", "with_underscore", "-leading", "trailing-", "two--dashes", "x" * 64],
    )
    def test_invalid_names_rejected(self, name: str) -> None:
        candidate = CustomProviderConfig(**{**OFFICE_PROVIDER, "name": name})
        with pytest.raises(ValueError, match="name"):
            validate_custom_provider(candidate)

    @pytest.mark.parametrize("name", list(PROVIDERS))
    def test_builtin_names_are_reserved(self, name: str) -> None:
        candidate = CustomProviderConfig(**{**OFFICE_PROVIDER, "name": name})
        with pytest.raises(ValueError, match="reserved"):
            validate_custom_provider(candidate)

    def test_duplicate_names_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            build_provider_registry([OFFICE_PROVIDER, OFFICE_PROVIDER])

    @pytest.mark.parametrize("model", ["", "   ", "m" * 257])
    def test_invalid_default_model_rejected(self, model: str) -> None:
        candidate = CustomProviderConfig(**{**OFFICE_PROVIDER, "default_model": model})
        with pytest.raises(ValueError, match="default_model"):
            validate_custom_provider(candidate)

    @pytest.mark.parametrize("max_tokens", [True, 0, -1, 1_000_001])
    def test_invalid_max_tokens_rejected(self, max_tokens: int) -> None:
        candidate = CustomProviderConfig(**{**OFFICE_PROVIDER, "max_tokens": max_tokens})
        with pytest.raises(ValueError, match="max_tokens"):
            validate_custom_provider(candidate)

    def test_more_than_one_hundred_entries_rejected(self) -> None:
        configs = [
            CustomProviderConfig(**{**OFFICE_PROVIDER, "name": f"provider-{index}"})
            for index in range(101)
        ]
        with pytest.raises(ValueError, match="100"):
            build_provider_registry(configs)


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
            "https://",  # scheme without a hostname
            "https:///v1",  # empty hostname, path only
            "https://user:secret@llm.example.com/v1",
            "https://llm.example.com/v1?api_key=secret",
            "https://llm.example.com/v1#secret",
        ],
    )
    def test_rejects_everything_else(self, url: str) -> None:
        with pytest.raises(ValueError, match="custom provider"):
            validate_custom_url(url)
