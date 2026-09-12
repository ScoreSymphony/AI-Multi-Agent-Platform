from __future__ import annotations

import json
import os
from urllib import error as urlerror
from urllib import request as urlrequest

import pytest


@pytest.mark.integration
def test_live_bifrost_blocks_configured_ssrf_targets_before_connection() -> None:
    bifrost_base_url = os.getenv("BIFROST_EVAL_BIFROST_BASE_URL")
    native_openai_model = os.getenv("BIFROST_EVAL_BIFROST_NATIVE_OPENAI_MODEL")
    blocked_urls_raw = os.getenv("BIFROST_EVAL_SSRF_BLOCKED_URLS_JSON")
    sentinel_control_url = os.getenv("BIFROST_EVAL_SSRF_SENTINEL_CONTROL_URL")
    if not all(
        (
            bifrost_base_url,
            native_openai_model,
            blocked_urls_raw,
            sentinel_control_url,
        )
    ):
        pytest.skip("live #859 deployment-level SSRF environment is not configured")

    assert bifrost_base_url is not None
    assert native_openai_model is not None
    assert blocked_urls_raw is not None
    assert sentinel_control_url is not None

    blocked_urls = _blocked_urls(blocked_urls_raw)
    _reset_sentinel(sentinel_control_url)
    assert _sentinel_hits(sentinel_control_url) == 0

    for blocked_url in blocked_urls:
        status, _ = _bifrost_file_url_request(
            base_url=bifrost_base_url,
            native_model=native_openai_model,
            blocked_url=blocked_url,
            api_key_env=os.getenv("BIFROST_EVAL_BIFROST_API_KEY_ENV"),
        )
        assert 400 <= status < 600, (
            f"Bifrost unexpectedly accepted SSRF probe target {blocked_url!r} "
            f"with HTTP status {status}"
        )
        assert _sentinel_hits(sentinel_control_url) == 0, (
            f"Bifrost connected to controlled blocked target {blocked_url!r}"
        )


def _blocked_urls(raw: str) -> list[str]:
    parsed = json.loads(raw)
    if not isinstance(parsed, list) or not parsed:
        raise AssertionError("BIFROST_EVAL_SSRF_BLOCKED_URLS_JSON must be a non-empty JSON list")
    urls: list[str] = []
    for item in parsed:
        if not isinstance(item, str) or not item.startswith(("http://", "https://")):
            raise AssertionError("every SSRF probe target must be an http(s) URL string")
        urls.append(item)
    return urls


def _bifrost_file_url_request(
    *,
    base_url: str,
    native_model: str,
    blocked_url: str,
    api_key_env: str | None,
) -> tuple[int, str]:
    payload = json.dumps(
        {
            "model": native_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Read the attached controlled test file."},
                        {"type": "file", "file": {"file_url": blocked_url}},
                    ],
                }
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key_env:
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise AssertionError(f"configured API key environment variable {api_key_env!r} is empty")
        headers["Authorization"] = f"Bearer {api_key}"

    request = urlrequest.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=10.0) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urlerror.URLError as exc:
        raise AssertionError("Bifrost SSRF probe endpoint was unreachable") from exc


def _reset_sentinel(base_url: str) -> None:
    request = urlrequest.Request(
        f"{base_url.rstrip('/')}/reset",
        data=b"",
        method="POST",
    )
    with urlrequest.urlopen(request, timeout=5.0) as response:
        assert response.status == 200


def _sentinel_hits(base_url: str) -> int:
    with urlrequest.urlopen(f"{base_url.rstrip('/')}/hits", timeout=5.0) as response:
        payload = json.load(response)
    hits = payload.get("hits")
    assert isinstance(hits, int)
    return hits
