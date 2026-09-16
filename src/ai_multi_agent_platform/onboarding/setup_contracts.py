"""Value-free browser setup field projections for canonical onboarding commands.

The browser must not invent provider/configuration fields independently from the backend. These
projections describe the already-existing ``onboarding.configure-model`` payload without owning a
second configuration model. Secret fields describe only ``SecretReference`` metadata; secret
material itself is never part of this contract.
"""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue

MODEL_SETUP_CONTRACT_VERSION = "1"


def model_setup_contract(installed_adapter_ids: list[str]) -> dict[str, JsonValue]:
    """Project the canonical configure-model command into safe browser form metadata."""

    return {
        "version": MODEL_SETUP_CONTRACT_VERSION,
        "command": "onboarding.configure-model",
        "fields": [
            _field(
                "adapter_id",
                "Installed adapter",
                "select",
                required=True,
                options=installed_adapter_ids,
            ),
            _field(
                "location",
                "Location",
                "select",
                required=True,
                options=["local", "self_hosted"],
            ),
            _field("provider_id", "Provider ID", "text", required=True),
            _field("model_config_id", "Model configuration ID", "text", required=True),
            _field("provider_model", "Provider-native model name", "text", required=True),
            _field("display_name", "Display name", "text", required=False),
            _field(
                "base_url",
                "Base URL",
                "url",
                required=True,
                placeholder="http://127.0.0.1:PORT/...",
            ),
            _field("capabilities.context_window", "Context window", "integer", required=False),
            _field("capabilities.tool_calling", "Tool calling", "boolean", required=False),
            _field(
                "capabilities.structured_output",
                "Structured output",
                "boolean",
                required=False,
            ),
            _field("capabilities.streaming", "Streaming", "boolean", required=False),
            _field(
                "credential_ref.provider",
                "Secret provider",
                "text",
                required=False,
                secret_reference=True,
            ),
            _field(
                "credential_ref.secret_id",
                "Secret ID",
                "text",
                required=False,
                secret_reference=True,
            ),
            _field(
                "credential_ref.scope",
                "Secret scope",
                "text",
                required=False,
                secret_reference=True,
                placeholder="platform",
            ),
            _field(
                "credential_ref.version",
                "Secret version",
                "text",
                required=False,
                secret_reference=True,
            ),
        ],
        "secret_values_accepted": False,
        "automatic_remote_provider_selection": False,
        "automatic_paid_provider_selection": False,
    }


def _field(
    path: str,
    label: str,
    input_kind: str,
    *,
    required: bool,
    options: list[str] | None = None,
    placeholder: str | None = None,
    secret_reference: bool = False,
) -> dict[str, JsonValue]:
    return {
        "path": path,
        "label": label,
        "input_kind": input_kind,
        "required": required,
        "options": list(options or ()),
        "placeholder": placeholder,
        "secret_reference": secret_reference,
    }


__all__ = ["MODEL_SETUP_CONTRACT_VERSION", "model_setup_contract"]
