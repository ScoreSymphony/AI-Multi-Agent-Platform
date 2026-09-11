from __future__ import annotations

import json
import tomllib
from pathlib import Path

from jsonschema import Draft202012Validator

from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    HealthStatus,
    ProviderDescriptor,
)
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    RoutingRequirements,
)
from ai_multi_agent_platform.testing import FakeModelProvider

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_PATH = ROOT / "config" / "inference-backend-evaluation.sglang-v0.5.19.json"
UPSTREAM_EVIDENCE_PATH = ROOT / "config" / "inference-backend-upstream.sglang-v0.5.19.json"
REPORT_SCHEMA_PATH = (
    ROOT
    / "src"
    / "ai_multi_agent_platform"
    / "benchmarking"
    / "schemas"
    / "inference-backend-evaluation-report.v1.schema.json"
)


class BaselineLocalProvider(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="baseline-local",
        provider_type="model",
        supported_operations=("generate",),
        capabilities=(
            Capability(
                name="model.text",
                kind=CapabilityKind.MODEL,
                supported_operations=("generate",),
                modalities=("text",),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )


class SGLangCandidateProvider(FakeModelProvider):
    """Contract-only stand-in; this test must not import an SGLang package."""

    descriptor = ProviderDescriptor(
        provider_id="sglang-candidate",
        provider_type="model",
        supported_operations=("generate",),
        capabilities=(
            Capability(
                name="model.text",
                kind=CapabilityKind.MODEL,
                supported_operations=("generate",),
                modalities=("text",),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )


def _model(config_id: str, provider_id: str, priority: int) -> ModelConfiguration:
    return ModelConfiguration(
        config_id=config_id,
        display_name=config_id,
        provider_id=provider_id,
        location=ModelLocation.LOCAL,
        capabilities=ModelCapabilities(
            context_window=8192,
            tool_calling=True,
            structured_output=True,
            streaming=True,
            modalities=("text",),
        ),
        health=HealthStatus.HEALTHY,
        priority=priority,
    )


def test_sglang_campaign_is_pinned_and_does_not_claim_a_decision_without_measurements() -> None:
    campaign = json.loads(CAMPAIGN_PATH.read_text(encoding="utf-8"))

    assert campaign["schema_version"] == "1.0"
    assert campaign["issue"] == 860
    assert campaign["status"] == "in_progress"
    assert campaign["candidate"] == {
        "backend": "sglang",
        "upstream": "https://github.com/sgl-project/sglang",
        "release": "v0.5.19",
        "release_commit": "0bcd822377da7b5718e674eaf9c870d349424dd1",
        "license": "Apache-2.0",
        "integration_boundary": "ModelProvider",
        "mandatory_dependency": False,
    }
    assert campaign["decision"]["outcome"] is None
    assert set(campaign["decision"]["allowed_outcomes"]) == {
        "supported_optional",
        "experimental_only",
        "reject/defer",
    }
    assert campaign["decision"]["requires_live_measurements"] is True


def test_sglang_upstream_evidence_matches_campaign_pin() -> None:
    campaign = json.loads(CAMPAIGN_PATH.read_text(encoding="utf-8"))
    upstream = json.loads(UPSTREAM_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert upstream["backend"] == campaign["candidate"]["backend"]
    assert upstream["release"] == campaign["candidate"]["release"]
    assert upstream["release_commit_sha"] == campaign["candidate"]["release_commit"]
    assert upstream["license"] == campaign["candidate"]["license"]
    assert len(upstream["release_commit_sha"]) == 40
    assert len(upstream["annotated_tag_sha"]) == 40
    assert len(upstream["license_blob_sha"]) == 40


def test_sglang_campaign_covers_required_contract_failure_and_comparison_dimensions() -> None:
    campaign = json.loads(CAMPAIGN_PATH.read_text(encoding="utf-8"))

    assert {entry["backend"] for entry in campaign["comparison_backends"]} == {
        "sglang",
        "vllm",
        "llama.cpp-or-ollama",
    }
    assert {
        "chat-completion-non-streaming",
        "chat-completion-streaming",
        "stream-cancellation",
        "structured-json-output",
        "tool-call-round-trip",
        "canonical-model-id-stability",
        "candidate-absent-baseline-routing",
    } <= set(campaign["contract_cases"])
    assert {
        "endpoint-unavailable-before-dispatch",
        "server-termination-during-stream",
        "canonical-cancellation",
        "model-load-failure",
        "bounded-oom-or-resource-exhaustion",
        "process-restart-readiness-recovery",
        "remote-worker-loss",
    } <= set(campaign["failure_cases"])
    assert {
        "single-gpu-worker",
        "multi-gpu-single-worker",
        "remote-gpu-worker",
    } <= set(campaign["placement_cases"])
    assert {
        "request_throughput_per_second",
        "ttft_p95_ms",
        "itl_or_tpot_p95_ms",
        "peak_vram_bytes",
        "peak_host_ram_bytes",
        "error_count",
    } <= set(campaign["required_metrics"])
    assert campaign["raw_evidence"]["summary_only_is_sufficient"] is False


def test_inference_backend_report_schema_is_valid_and_covers_campaign_fields() -> None:
    campaign = json.loads(CAMPAIGN_PATH.read_text(encoding="utf-8"))
    schema = json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    assert set(campaign["required_metrics"]) <= set(
        schema["properties"]["metrics"]["required"]
    )
    report_environment_fields = {
        "platform_commit",
        "backend_revision",
        *schema["properties"]["model"]["required"],
        *schema["properties"]["environment"]["required"],
        *schema["properties"]["workload"]["required"],
    }
    assert set(campaign["required_environment_fields"]) <= report_environment_fields
    assert set(schema["properties"]["backend"]["enum"]) == {
        "sglang",
        "vllm",
        "llama.cpp",
        "ollama",
    }


def test_sglang_is_not_a_mandatory_python_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    required_dependencies = tuple(str(item).lower() for item in project["dependencies"])

    assert not any(dependency.startswith("sglang") for dependency in required_dependencies)


def test_candidate_absence_preserves_baseline_routing_and_canonical_identity() -> None:
    registry = ModelRegistry()
    registry.register_provider(BaselineLocalProvider())
    registry.register_provider(SGLangCandidateProvider())
    registry.register_model(_model("model-baseline", "baseline-local", priority=10))
    registry.register_model(_model("model-sglang-eval", "sglang-candidate", priority=20))
    router = DeterministicModelRouter(registry)

    candidate_route = router.route(RoutingRequirements(local_only=True))
    assert candidate_route.model_config_id == "model-sglang-eval"

    registry.unregister_provider("sglang-candidate")

    baseline_route = router.route(RoutingRequirements(local_only=True))
    assert baseline_route.model_config_id == "model-baseline"
    assert registry.get_model("model-sglang-eval").config_id == "model-sglang-eval"
