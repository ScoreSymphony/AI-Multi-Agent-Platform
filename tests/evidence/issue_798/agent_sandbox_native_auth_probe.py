#!/usr/bin/env python3
"""Capture read-only native Agent-Sandbox object-ownership evidence for #798.

This probe deliberately exercises only native management GET surfaces. Mutating
pause/resume/file/terminal/delete authorization remains part of the disposable live
campaign and is not triggered by this script.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

EVALUATED_REVISION = "d1b7ac007debcb1ba8de91c76afb49bee90d096a"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-native-base-url", required=True)
    parser.add_argument("--sandbox-a-name", required=True)
    parser.add_argument("--sandbox-b-name", required=True)
    parser.add_argument("--token-a-env", default="AGENT_SANDBOX_TOKEN_A")
    parser.add_argument("--token-b-env", default="AGENT_SANDBOX_TOKEN_B")
    parser.add_argument("--file-list-path", default="/")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _request(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"X-Api-Key": token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return {
                "status": response.status,
                "authorized": 200 <= response.status < 300,
                "transport_error": None,
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "authorized": False,
            "transport_error": None,
        }
    except urllib.error.URLError as exc:
        return {
            "status": None,
            "authorized": None,
            "transport_error": type(exc.reason).__name__,
        }


def _surface_urls(base_url: str, sandbox_name: str, file_list_path: str) -> dict[str, str]:
    base = base_url.rstrip("/")
    quoted_name = urllib.parse.quote(sandbox_name, safe="")
    query = urllib.parse.urlencode({"path": file_list_path})
    return {
        "native_sandbox_get": f"{base}/sandbox/{quoted_name}",
        "native_logs_get": f"{base}/logs/sandbox/{quoted_name}?tailLines=1",
        "native_files_list": f"{base}/sandbox/files/{quoted_name}?{query}",
    }


def _probe_matrix(
    *,
    base_url: str,
    sandbox_a: str,
    sandbox_b: str,
    token_a: str,
    token_b: str,
    file_list_path: str,
    requester: Callable[[str, str], dict[str, Any]] = _request,
) -> dict[str, Any]:
    urls_a = _surface_urls(base_url, sandbox_a, file_list_path)
    urls_b = _surface_urls(base_url, sandbox_b, file_list_path)
    surfaces: dict[str, Any] = {}

    for surface in sorted(urls_a):
        a_to_a = requester(urls_a[surface], token_a)
        b_to_b = requester(urls_b[surface], token_b)
        a_to_b = requester(urls_b[surface], token_a)
        b_to_a = requester(urls_a[surface], token_b)
        all_results = (a_to_a, b_to_b, a_to_b, b_to_a)
        transport_complete = all(item.get("transport_error") is None for item in all_results)
        same_tenant_access = a_to_a.get("authorized") is True and b_to_b.get("authorized") is True
        cross_tenant_blocked = (
            a_to_b.get("authorized") is False and b_to_a.get("authorized") is False
        )
        surfaces[surface] = {
            "transport_complete": transport_complete,
            "same_tenant": {"a_to_a": a_to_a, "b_to_b": b_to_b},
            "cross_tenant": {"a_to_b": a_to_b, "b_to_a": b_to_a},
            "same_tenant_access": same_tenant_access,
            "cross_tenant_blocked": cross_tenant_blocked,
            "ownership_shape_passed": (
                transport_complete and same_tenant_access and cross_tenant_blocked
            ),
        }

    return {
        "surfaces": surfaces,
        "all_transport_complete": all(value["transport_complete"] for value in surfaces.values()),
        "all_same_tenant_access": all(value["same_tenant_access"] for value in surfaces.values()),
        "all_cross_tenant_blocked": all(
            value["cross_tenant_blocked"] for value in surfaces.values()
        ),
        "all_ownership_shapes_passed": all(
            value["ownership_shape_passed"] for value in surfaces.values()
        ),
    }


def _main() -> int:
    args = _args()
    token_a = os.environ.get(args.token_a_env, "")
    token_b = os.environ.get(args.token_b_env, "")
    if not token_a or not token_b:
        raise SystemExit(
            "provider authorization token environment variables are missing: "
            f"{args.token_a_env}, {args.token_b_env}"
        )

    matrix = _probe_matrix(
        base_url=args.provider_native_base_url,
        sandbox_a=args.sandbox_a_name,
        sandbox_b=args.sandbox_b_name,
        token_a=token_a,
        token_b=token_b,
        file_list_path=args.file_list_path,
    )
    report = {
        "schema_version": 1,
        "issue": 798,
        "provider": "agent-sandbox",
        "evaluated_revision": EVALUATED_REVISION,
        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        "probe_kind": "native_read_only_object_ownership",
        "token_env_names": [args.token_a_env, args.token_b_env],
        "token_values_retained": False,
        "sandbox_names": {"a": args.sandbox_a_name, "b": args.sandbox_b_name},
        "matrix": matrix,
        "expected": (
            "same-tenant GET access succeeds while both cross-token directions are rejected "
            "for every native read-only surface"
        ),
        "limitations": (
            "Read-only evidence does not cover terminal/router execution, upload/delete, "
            "pause/resume, snapshot or sandbox deletion. Those remain disposable live-campaign "
            "requirements."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
