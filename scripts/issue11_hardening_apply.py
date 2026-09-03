from __future__ import annotations

import json
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text()
    if old not in text:
        raise SystemExit(f"expected patch anchor missing in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1))


adapter = "src/ai_multi_agent_platform/adapters/litellm.py"

replace_once(
    adapter,
    '    LIBRARY = "library"\n    PROXY = "proxy"\n\n\n@dataclass(frozen=True, slots=True)\nclass LiteLLMProviderConfig:',
    '    LIBRARY = "library"\n    PROXY = "proxy"\n\n\nclass LiteLLMTelemetryMode(StrEnum):\n'
    '    """Adapter-owned telemetry/logging metadata policy."""\n\n'
    '    PLATFORM_ONLY = "platform_only"\n'
    '    DISABLED = "disabled"\n\n\n'
    '@dataclass(frozen=True, slots=True)\nclass LiteLLMProviderConfig:',
)

replace_once(
    adapter,
    '    timeout_seconds: float = 120.0\n    extra_headers: Mapping[str, str] = field(default_factory=dict)\n',
    '    timeout_seconds: float = 120.0\n'
    '    max_retries: int = 0\n'
    '    telemetry_mode: LiteLLMTelemetryMode = LiteLLMTelemetryMode.PLATFORM_ONLY\n'
    '    extra_headers: Mapping[str, str] = field(default_factory=dict)\n',
)

replace_once(
    adapter,
    '        if self.timeout_seconds <= 0:\n'
    '            raise ValueError("timeout_seconds must be greater than zero")\n',
    '        if self.timeout_seconds <= 0:\n'
    '            raise ValueError("timeout_seconds must be greater than zero")\n'
    '        if isinstance(self.max_retries, bool) or self.max_retries < 0:\n'
    '            raise ValueError("max_retries must be a non-negative integer")\n'
    '        if not isinstance(self.telemetry_mode, LiteLLMTelemetryMode):\n'
    '            raise ValueError("telemetry_mode must be a LiteLLMTelemetryMode")\n',
)

replace_once(
    adapter,
    '        object.__setattr__(self, "models", MappingProxyType(dict(self.models)))\n'
    '        object.__setattr__(self, "extra_headers", MappingProxyType(dict(self.extra_headers)))\n\n\n'
    'class LiteLLMModelProvider',
    '''        object.__setattr__(self, "models", MappingProxyType(dict(self.models)))
        object.__setattr__(self, "extra_headers", MappingProxyType(dict(self.extra_headers)))

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> LiteLLMProviderConfig:
        """Build validated adapter configuration from resolved JSON-like values."""

        allowed = {
            "provider_id",
            "mode",
            "models",
            "enabled",
            "base_url",
            "api_key_env",
            "timeout_seconds",
            "max_retries",
            "telemetry_mode",
            "extra_headers",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown LiteLLM configuration fields: {unknown!r}")

        def string_field(name: str, *, required: bool = False) -> str | None:
            raw = values.get(name)
            if raw is None:
                if required:
                    raise ValueError(f"{name} is required")
                return None
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"{name} must be a non-blank string")
            return raw

        provider_id = string_field("provider_id", required=True)
        mode_value = string_field("mode", required=True)
        assert provider_id is not None
        assert mode_value is not None
        try:
            mode = LiteLLMMode(mode_value)
        except ValueError as exc:
            raise ValueError(f"unsupported LiteLLM mode: {mode_value}") from exc

        models_raw = values.get("models")
        if not isinstance(models_raw, Mapping):
            raise ValueError("models must be an object mapping canonical IDs to provider model names")
        models: dict[str, str] = {}
        for raw_key, raw_value in models_raw.items():
            if not isinstance(raw_key, str) or not isinstance(raw_value, str):
                raise ValueError("models must contain string keys and values")
            models[raw_key] = raw_value

        enabled = values.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")

        timeout_raw = values.get("timeout_seconds", 120.0)
        if isinstance(timeout_raw, bool) or not isinstance(timeout_raw, (int, float)):
            raise ValueError("timeout_seconds must be numeric")

        retries_raw = values.get("max_retries", 0)
        if isinstance(retries_raw, bool) or not isinstance(retries_raw, int):
            raise ValueError("max_retries must be an integer")

        telemetry_raw = values.get(
            "telemetry_mode",
            LiteLLMTelemetryMode.PLATFORM_ONLY.value,
        )
        if not isinstance(telemetry_raw, str):
            raise ValueError("telemetry_mode must be a string")
        try:
            telemetry_mode = LiteLLMTelemetryMode(telemetry_raw)
        except ValueError as exc:
            raise ValueError(f"unsupported LiteLLM telemetry_mode: {telemetry_raw}") from exc

        headers_raw = values.get("extra_headers", {})
        if not isinstance(headers_raw, Mapping):
            raise ValueError("extra_headers must be an object")
        extra_headers: dict[str, str] = {}
        for raw_key, raw_value in headers_raw.items():
            if not isinstance(raw_key, str) or not isinstance(raw_value, str):
                raise ValueError("extra_headers must contain string keys and values")
            extra_headers[raw_key] = raw_value

        return cls(
            provider_id=provider_id,
            mode=mode,
            models=models,
            enabled=enabled,
            base_url=string_field("base_url"),
            api_key_env=string_field("api_key_env"),
            timeout_seconds=float(timeout_raw),
            max_retries=retries_raw,
            telemetry_mode=telemetry_mode,
            extra_headers=extra_headers,
        )


class LiteLLMModelProvider''',
)

replace_once(
    adapter,
    '                transport=proxy_transport,\n            )\n\n    @property\n',
    '                transport=proxy_transport,\n            )\n'
    '        elif config.enabled and completion is None:\n'
    '            # Core imports remain dependency-free, but an enabled library\n'
    '            # provider must be executable before it can be registered.\n'
    '            self._completion = self._resolve_completion()\n\n'
    '    @property\n',
)

replace_once(
    adapter,
    '            "enabled": self.config.enabled,\n            "dependency": "optional",\n',
    '            "enabled": self.config.enabled,\n'
    '            "dependency": "optional",\n'
    '            "max_retries": self.config.max_retries,\n'
    '            "telemetry_mode": self.config.telemetry_mode.value,\n',
)

replace_once(
    adapter,
    '        payload: dict[str, object] = {\n'
    '            "model": native_model,\n'
    '            "messages": self._messages(request_data),\n'
    '            "stream": False,\n'
    '        }\n',
    '        payload: dict[str, object] = {\n'
    '            "model": native_model,\n'
    '            "messages": self._messages(request_data),\n'
    '            "stream": False,\n'
    '            "num_retries": self.config.max_retries,\n'
    '        }\n',
)

replace_once(
    adapter,
    '''        return ModelResponse(
            request_id=request_data.request_id,
            text=content,
            model_ref=canonical_model_id,
            usage=usage,
            adapter_metadata=(
                AdapterMetadata(namespace="litellm", values=adapter_values),
                AdapterMetadata(namespace="model-protocol", values=protocol_values),
            ),
        )
''',
    '''        adapter_metadata: list[AdapterMetadata] = [
            AdapterMetadata(namespace="model-protocol", values=protocol_values)
        ]
        if self.config.telemetry_mode is LiteLLMTelemetryMode.PLATFORM_ONLY:
            adapter_metadata.insert(0, AdapterMetadata(namespace="litellm", values=adapter_values))

        return ModelResponse(
            request_id=request_data.request_id,
            text=content,
            model_ref=canonical_model_id,
            usage=usage,
            adapter_metadata=tuple(adapter_metadata),
        )
''',
)

replace_once(
    adapter,
    '    ) -> ModelResponse:\n        values: dict[str, JsonValue] = {\n            "mode": LiteLLMMode.PROXY.value,\n',
    '    ) -> ModelResponse:\n'
    '        if self.config.telemetry_mode is LiteLLMTelemetryMode.DISABLED:\n'
    '            return response\n\n'
    '        values: dict[str, JsonValue] = {\n'
    '            "mode": LiteLLMMode.PROXY.value,\n',
)

replace_once(
    adapter,
    '''        return ContractError(
            code,
            f"LiteLLM request failed: {name}",
            retryable=retryable,
            provider_id=self.config.provider_id,
            details={"exception_type": name},
            adapter_metadata=(
                AdapterMetadata(
                    namespace="litellm",
                    values={"exception_type": name, "mode": self.config.mode.value},
                ),
            ),
        )
''',
    '''        adapter_metadata: tuple[AdapterMetadata, ...] = ()
        if self.config.telemetry_mode is LiteLLMTelemetryMode.PLATFORM_ONLY:
            adapter_metadata = (
                AdapterMetadata(
                    namespace="litellm",
                    values={"exception_type": name, "mode": self.config.mode.value},
                ),
            )

        return ContractError(
            code,
            f"LiteLLM request failed: {name}",
            retryable=retryable,
            provider_id=self.config.provider_id,
            details={"exception_type": name},
            adapter_metadata=adapter_metadata,
        )
''',
)

init_file = "src/ai_multi_agent_platform/adapters/__init__.py"
replace_once(
    init_file,
    'from .litellm import LiteLLMMode, LiteLLMModelProvider, LiteLLMProviderConfig\n',
    '''from .litellm import (
    LiteLLMMode,
    LiteLLMModelProvider,
    LiteLLMProviderConfig,
    LiteLLMTelemetryMode,
)
''',
)
replace_once(
    init_file,
    '    "LiteLLMProviderConfig",\n',
    '    "LiteLLMProviderConfig",\n    "LiteLLMTelemetryMode",\n',
)

config_path = Path("config/litellm.example.json")
config_data = json.loads(config_path.read_text())
for example in config_data.values():
    example["max_retries"] = 0
    example["telemetry_mode"] = "platform_only"
config_path.write_text(json.dumps(config_data, indent=2) + "\n")

# Existing dependency-absence test must wrap provider creation now that enabled
# library providers validate their optional dependency during construction.
existing_tests = "tests/test_issue_11_litellm_adapter.py"
replace_once(
    existing_tests,
    '''def test_library_mode_fails_clearly_when_optional_dependency_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-library",
            mode=LiteLLMMode.LIBRARY,
            models={"model-local-coder": "ollama/qwen3-coder"},
        )
    )

    def missing_dependency(name: str) -> object:
        assert name == "litellm"
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(litellm_adapter.importlib, "import_module", missing_dependency)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            provider.generate(
                ModelRequest(
                    request_id="req-litellm-missing",
                    messages=("hello",),
                    context=CTX,
                    requirements={"model_config_id": "model-local-coder"},
                )
            )
        )

    assert captured.value.code is ErrorCode.INVALID_CONFIGURATION
    assert captured.value.details["install_extra"] == "ai-multi-agent-platform[litellm]"
''',
    '''def test_library_mode_fails_clearly_when_optional_dependency_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_dependency(name: str) -> object:
        assert name == "litellm"
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(litellm_adapter.importlib, "import_module", missing_dependency)

    with pytest.raises(ContractError) as captured:
        LiteLLMModelProvider(
            LiteLLMProviderConfig(
                provider_id="litellm-library",
                mode=LiteLLMMode.LIBRARY,
                models={"model-local-coder": "ollama/qwen3-coder"},
            )
        )

    assert captured.value.code is ErrorCode.INVALID_CONFIGURATION
    assert captured.value.details["install_extra"] == "ai-multi-agent-platform[litellm]"
''',
)

Path("tests/test_issue_11_completion_hardening.py").write_text(
    '''from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import pytest

import ai_multi_agent_platform.adapters.litellm as litellm_adapter
from ai_multi_agent_platform.adapters.litellm import (
    LiteLLMMode,
    LiteLLMModelProvider,
    LiteLLMProviderConfig,
    LiteLLMTelemetryMode,
)
from ai_multi_agent_platform.configuration import (
    ConfigLayer,
    ConfigScope,
    ConfigSource,
    ConfigurationResolver,
    ConfigurationSchema,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelRequest,
    OperationContext,
)
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)

CTX = OperationContext(correlation_id="corr-litellm-hardening")


async def successful_completion(**kwargs: object) -> object:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ]
    }


def test_committed_examples_flow_through_platform_configuration() -> None:
    config_file = Path(__file__).parents[1] / "config" / "litellm.example.json"
    examples = json.loads(config_file.read_text())
    raw_library = examples["library_local_example"]

    schema = ConfigurationSchema(
        version="issue-11-v1",
        json_schema={
            "type": "object",
            "properties": {"litellm": {"type": "object"}},
            "required": ["litellm"],
            "additionalProperties": False,
        },
    )
    effective = ConfigurationResolver(schema).resolve(
        (
            ConfigLayer(
                ConfigScope.PROVIDER,
                {"litellm": raw_library},
                ConfigSource("issue-11-example", "json"),
            ),
        )
    )
    raw_effective = effective.values["litellm"]
    assert isinstance(raw_effective, dict)
    parsed = LiteLLMProviderConfig.from_mapping(raw_effective)

    assert parsed.enabled is True
    assert parsed.mode is LiteLLMMode.LIBRARY
    assert parsed.max_retries == 0
    assert parsed.telemetry_mode is LiteLLMTelemetryMode.PLATFORM_ONLY


def test_from_mapping_rejects_unknown_configuration() -> None:
    with pytest.raises(ValueError, match="unknown LiteLLM configuration fields"):
        LiteLLMProviderConfig.from_mapping(
            {
                "provider_id": "litellm-test",
                "mode": "library",
                "models": {"model-local": "ollama/model-local"},
                "silent_policy_bypass": True,
            }
        )


def test_library_retry_configuration_is_forwarded_explicitly() -> None:
    captured: dict[str, object] = {}

    async def completion(**kwargs: object) -> object:
        captured.update(kwargs)
        return await successful_completion(**kwargs)

    provider = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-retries",
            mode=LiteLLMMode.LIBRARY,
            models={"model-local": "ollama/model-local"},
            max_retries=3,
        ),
        completion=completion,
    )
    asyncio.run(
        provider.generate(
            ModelRequest(
                request_id="req-retries",
                messages=("hello",),
                context=CTX,
                requirements={"model_config_id": "model-local"},
            )
        )
    )

    assert captured["num_retries"] == 3


def test_telemetry_disabled_suppresses_litellm_request_metadata() -> None:
    provider = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-no-telemetry",
            mode=LiteLLMMode.LIBRARY,
            models={"model-local": "ollama/model-local"},
            telemetry_mode=LiteLLMTelemetryMode.DISABLED,
        ),
        completion=successful_completion,
    )
    response = asyncio.run(
        provider.generate(
            ModelRequest(
                request_id="req-no-telemetry",
                messages=("hello",),
                context=CTX,
                requirements={"model_config_id": "model-local"},
            )
        )
    )

    assert {item.namespace for item in response.adapter_metadata} == {"model-protocol"}
    assert provider.descriptor.adapter_metadata[0].values["telemetry_mode"] == "disabled"


def test_enabled_library_provider_fails_before_registry_attachment_without_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_dependency(name: str) -> object:
        assert name == "litellm"
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(litellm_adapter.importlib, "import_module", missing_dependency)
    registry = ModelRegistry()

    with pytest.raises(ContractError) as captured:
        provider = LiteLLMModelProvider(
            LiteLLMProviderConfig(
                provider_id="litellm-missing",
                mode=LiteLLMMode.LIBRARY,
                models={"model-local": "ollama/model-local"},
            )
        )
        registry.register_provider(provider)

    assert captured.value.code is ErrorCode.INVALID_CONFIGURATION
    assert registry.list_providers() == ()


def test_disabled_library_provider_does_not_load_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def missing_dependency(name: str) -> object:
        calls.append(name)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(litellm_adapter.importlib, "import_module", missing_dependency)
    provider = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-disabled",
            mode=LiteLLMMode.LIBRARY,
            models={"model-local": "ollama/model-local"},
            enabled=False,
        )
    )

    assert asyncio.run(provider.health()) is HealthStatus.UNAVAILABLE
    assert calls == []


def test_platform_router_preserves_self_hosted_only_policy() -> None:
    registry = ModelRegistry()
    self_hosted = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-self-hosted",
            mode=LiteLLMMode.LIBRARY,
            models={"model-self-hosted": "ollama/model-self-hosted"},
        ),
        completion=successful_completion,
    )
    remote = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-remote",
            mode=LiteLLMMode.LIBRARY,
            models={"model-remote": "openai/model-remote"},
        ),
        completion=successful_completion,
    )
    registry.register_provider(self_hosted)
    registry.register_provider(remote)
    asyncio.run(registry.refresh_health())
    registry.register_model(
        ModelConfiguration(
            config_id="model-self-hosted",
            display_name="Self hosted",
            provider_id="litellm-self-hosted",
            location=ModelLocation.SELF_HOSTED,
            health=HealthStatus.HEALTHY,
            priority=10,
        )
    )
    registry.register_model(
        ModelConfiguration(
            config_id="model-remote",
            display_name="Remote",
            provider_id="litellm-remote",
            location=ModelLocation.REMOTE,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )

    selection = asyncio.run(
        DeterministicModelRouter(registry).select_provider(
            ModelRequest(
                request_id="req-self-hosted",
                messages=("hello",),
                context=CTX,
                requirements={"self_hosted_only": True},
            )
        )
    )

    assert selection.model_ref == "model-self-hosted"
    assert selection.provider_id == "litellm-self-hosted"
'''
)

# Remove an unused import if Ruff detects it through the generated test.
test_path = Path("tests/test_issue_11_completion_hardening.py")
test_text = test_path.read_text().replace("import importlib\n", "")
test_path.write_text(test_text)

docs = "docs/LITELLM_ADAPTER.md"
replace_once(
    docs,
    '`LiteLLMModelProvider` lazily loads the optional `litellm` Python package and calls `acompletion` with translated canonical request data.\n',
    '`LiteLLMModelProvider` keeps module imports dependency-free. When an enabled library provider instance is created without an injected test transport, it resolves the optional `litellm` package immediately so missing dependencies fail before registry attachment. It then calls `acompletion` with translated canonical request data.\n',
)
replace_once(
    docs,
    '- credential environment-variable reference;\n- timeout;\n- additional non-secret HTTP headers.\n',
    '- credential environment-variable reference;\n'
    '- timeout;\n'
    '- library-mode retry count (`max_retries` -> LiteLLM `num_retries`);\n'
    '- adapter telemetry/logging metadata mode (`platform_only` or `disabled`);\n'
    '- additional non-secret HTTP headers.\n\n'
    '`LiteLLMProviderConfig.from_mapping(...)` is the JSON/resolved-configuration boundary that turns provider-scope configuration into the validated runtime config object. Unknown keys are rejected. The committed examples are parsed through the platform configuration resolver and this boundary in tests.\n',
)

docs_path = Path(docs)
docs_text = docs_path.read_text()
marker = "## Local-first examples\n"
policy = '''## Retry, fallback, telemetry and locality policy

- Library mode forwards non-negative `max_retries` as LiteLLM `num_retries`; `0` is the local-first default. Request/control-plane timeouts still override the provider default timeout.
- Proxy mode does not add a second retry engine. Retry/load-balancing inside a separately deployed LiteLLM Proxy is deployment-owned and must not bypass the platform's canonical route selection.
- LiteLLM Router fallbacks remain intentionally disabled in this baseline. Provider fallbacks are therefore never silently enabled by adapter configuration.
- `telemetry_mode=platform_only` emits only platform-owned namespaced adapter metadata subject to platform redaction. `disabled` suppresses LiteLLM-specific per-request/error metadata. The adapter does not enable LiteLLM global callbacks or provider-native logging/telemetry.
- Local/self-hosted restrictions remain canonical `ModelConfiguration.location` plus `local_only` / `self_hosted_only` routing requirements. They are intentionally not duplicated as LiteLLM-native routing policy; tests prove a higher-priority remote LiteLLM-backed model cannot bypass either restriction.

'''
if "## Retry, fallback, telemetry and locality policy" not in docs_text:
    if marker not in docs_text:
        raise SystemExit("docs locality marker missing")
    docs_path.write_text(docs_text.replace(marker, policy + marker, 1))

# Temporary implementation machinery must not survive the hardening commit.
Path(".github/workflows/issue11-hardening-apply.yml").unlink(missing_ok=True)
Path("scripts/issue11_hardening_apply.py").unlink(missing_ok=True)
