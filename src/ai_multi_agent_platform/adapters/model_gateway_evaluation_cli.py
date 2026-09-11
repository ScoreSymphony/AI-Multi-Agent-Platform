"""Concrete OpenAI-compatible CLI for reproducible model-gateway benchmark evidence.

The provider-neutral benchmark contracts live in ``ai_multi_agent_platform.benchmarking``.
This composition entry point intentionally lives in the adapter layer because it wires those
contracts to the concrete OpenAI-compatible provider implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ai_multi_agent_platform.benchmarking.model_gateway_evaluation import (
    ModelGatewayBenchmarkSpec,
    run_model_gateway_comparison,
)
from ai_multi_agent_platform.benchmarking.model_gateway_evidence import (
    ModelGatewayTargetEvidence,
    build_model_gateway_evidence,
)

from .openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare one canonical model through a direct OpenAI-compatible endpoint "
            "and optional gateway endpoints without recording secret values."
        )
    )
    parser.add_argument("--canonical-model-id", required=True)
    parser.add_argument("--downstream-deployment-label", required=True)
    parser.add_argument("--direct-base-url", required=True)
    parser.add_argument("--direct-model", required=True)
    parser.add_argument("--direct-runtime-version", required=True)
    parser.add_argument("--direct-api-key-env")
    parser.add_argument("--bifrost-base-url", required=True)
    parser.add_argument("--bifrost-model", required=True)
    parser.add_argument("--bifrost-api-key-env")
    parser.add_argument("--bifrost-version", default="transports/v2.1.1")
    parser.add_argument("--bifrost-core-version", default="v1.8.6")
    parser.add_argument(
        "--bifrost-revision",
        default="c193745d2a713e9f58f021d43e138df5eb7e038a",
    )
    parser.add_argument("--litellm-base-url")
    parser.add_argument("--litellm-model")
    parser.add_argument("--litellm-api-key-env")
    parser.add_argument("--litellm-version")
    parser.add_argument("--operation-count", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--warmup-operations", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--prompt", default="Reply with the single word: ready")
    parser.add_argument("--platform-commit", default="unknown")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if (args.litellm_base_url is None) != (args.litellm_model is None):
        raise SystemExit("--litellm-base-url and --litellm-model must be supplied together")
    if args.litellm_api_key_env is not None and args.litellm_base_url is None:
        raise SystemExit("--litellm-api-key-env requires the LiteLLM endpoint arguments")
    if args.litellm_base_url is not None and args.litellm_version is None:
        raise SystemExit("--litellm-version is required when LiteLLM is included")

    spec = ModelGatewayBenchmarkSpec(
        canonical_model_id=args.canonical_model_id,
        operation_count=args.operation_count,
        concurrency=args.concurrency,
        warmup_operations=args.warmup_operations,
        timeout_seconds=args.timeout_seconds,
        prompt=args.prompt,
    )
    providers: dict[str, OpenAICompatibleModelProvider] = {
        "direct": _provider(
            provider_id="issue-859-direct",
            base_url=args.direct_base_url,
            canonical_model_id=args.canonical_model_id,
            native_model=args.direct_model,
            api_key_env=args.direct_api_key_env,
        ),
        "bifrost": _provider(
            provider_id="issue-859-bifrost",
            base_url=args.bifrost_base_url,
            canonical_model_id=args.canonical_model_id,
            native_model=args.bifrost_model,
            api_key_env=args.bifrost_api_key_env,
        ),
    }
    target_evidence: dict[str, ModelGatewayTargetEvidence] = {
        "direct": ModelGatewayTargetEvidence(
            component="direct-openai-compatible",
            component_version=args.direct_runtime_version,
            component_revision="not-applicable",
            native_model=args.direct_model,
            deployment_label=args.downstream_deployment_label,
        ),
        "bifrost": ModelGatewayTargetEvidence(
            component="bifrost-http",
            component_version=f"{args.bifrost_version} (core {args.bifrost_core_version})",
            component_revision=args.bifrost_revision,
            native_model=args.bifrost_model,
            deployment_label=args.downstream_deployment_label,
        ),
    }
    if args.litellm_base_url is not None and args.litellm_model is not None:
        assert args.litellm_version is not None
        providers["litellm"] = _provider(
            provider_id="issue-859-litellm",
            base_url=args.litellm_base_url,
            canonical_model_id=args.canonical_model_id,
            native_model=args.litellm_model,
            api_key_env=args.litellm_api_key_env,
        )
        target_evidence["litellm"] = ModelGatewayTargetEvidence(
            component="litellm",
            component_version=args.litellm_version,
            component_revision="not-recorded",
            native_model=args.litellm_model,
            deployment_label=args.downstream_deployment_label,
        )

    report = asyncio.run(
        run_model_gateway_comparison(
            spec=spec,
            providers=providers,
            platform_commit=args.platform_commit,
        )
    )
    payload = build_model_gateway_evidence(report, target_evidence=target_evidence)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


def _provider(
    *,
    provider_id: str,
    base_url: str,
    canonical_model_id: str,
    native_model: str,
    api_key_env: str | None,
) -> OpenAICompatibleModelProvider:
    return OpenAICompatibleModelProvider(
        OpenAICompatibleProviderConfig(
            provider_id=provider_id,
            base_url=base_url,
            models={canonical_model_id: native_model},
            api_key_env=api_key_env,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
