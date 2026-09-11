"""CLI for reproducible direct-vs-model-gateway benchmark evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ai_multi_agent_platform.adapters.openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)

from .model_gateway_evaluation import (
    ModelGatewayBenchmarkSpec,
    run_model_gateway_comparison,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare one canonical model through a direct OpenAI-compatible endpoint "
            "and optional gateway endpoints without recording secret values."
        )
    )
    parser.add_argument("--canonical-model-id", required=True)
    parser.add_argument("--direct-base-url", required=True)
    parser.add_argument("--direct-model", required=True)
    parser.add_argument("--direct-api-key-env")
    parser.add_argument("--bifrost-base-url", required=True)
    parser.add_argument("--bifrost-model", required=True)
    parser.add_argument("--bifrost-api-key-env")
    parser.add_argument("--litellm-base-url")
    parser.add_argument("--litellm-model")
    parser.add_argument("--litellm-api-key-env")
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
    if args.litellm_base_url is not None and args.litellm_model is not None:
        providers["litellm"] = _provider(
            provider_id="issue-859-litellm",
            base_url=args.litellm_base_url,
            canonical_model_id=args.canonical_model_id,
            native_model=args.litellm_model,
            api_key_env=args.litellm_api_key_env,
        )

    report = asyncio.run(
        run_model_gateway_comparison(
            spec=spec,
            providers=providers,
            platform_commit=args.platform_commit,
        )
    )
    rendered = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
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
