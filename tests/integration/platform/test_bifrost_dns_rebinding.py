from __future__ import annotations

import json
import os
from urllib import error as urlerror
from urllib import request as urlrequest

import pytest


@pytest.mark.integration
def test_live_bifrost_revalidates_dns_on_redirect_dial_and_blocks_rebound_loopback() -> None:
    bifrost_base_url = os.getenv("BIFROST_EVAL_BIFROST_BASE_URL")
    native_openai_model = os.getenv("BIFROST_EVAL_BIFROST_NATIVE_OPENAI_MODEL")
    rebinding_url = os.getenv("BIFROST_EVAL_SSRF_REBINDING_URL")
    dns_control_url = os.getenv("BIFROST_EVAL_REBINDING_DNS_CONTROL_URL")
    sentinel_control_url = os.getenv("BIFROST_EVAL_SSRF_SENTINEL_CONTROL_URL")
    if not all(
        (
            bifrost_base_url,
            native_openai_model,
            rebinding_url,
            dns_control_url,
            sentinel_control_url,
        )
    ):
        pytest.skip("live #859 DNS rebinding environment is not configured")

    assert bifrost_base_url is not None
    assert native_openai_model is not None
    assert rebinding_url is not None
    assert dns_control_url is not None
    assert sentinel_control_url is not None

    _post_empty(f"{dns_control_url.rstrip('/')}/reset")
    _post_empty(f"{sentinel_control_url.rstrip('/')}/reset")

    status, body = _bifrost_file_url_request(
        base_url=bifrost_base_url,
        native_model=native_openai_model,
        file_url=rebinding_url,
    )
    assert 400 <= status < 600, body[:500]
    assert "blocked connection to non-public address" in body.casefold(), body[:500]

    dns = _get_json(f"{dns_control_url.rstrip('/')}/stats")
    assert dns.get("a_queries") == 2, dns
    assert dns.get("a_answers") == ["203.0.113.10", "127.0.0.1"], dns

    sentinel = _get_json(f"{sentinel_control_url.rstrip('/')}/stats")
    paths = sentinel.get("paths")
    assert isinstance(paths, dict)
    assert paths.get("/redirect-to-rebound-host") == 1, sentinel
    assert paths.get("/ssrf-sentinel.txt", 0) == 0, sentinel


def _bifrost_file_url_request(
    *,
    base_url: str,
    native_model: str,
    file_url: str,
) -> tuple[int, str]:
    payload = json.dumps(
        {
            "model": native_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Read the controlled test file."},
                        {"type": "file", "file": {"file_url": file_url}},
                    ],
                }
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = urlrequest.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=10.0) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urlerror.URLError as exc:
        raise AssertionError("Bifrost DNS rebinding probe endpoint was unreachable") from exc


def _post_empty(url: str) -> None:
    request = urlrequest.Request(url, data=b"", method="POST")
    with urlrequest.urlopen(request, timeout=5.0) as response:
        assert response.status == 200


def _get_json(url: str) -> dict[str, object]:
    with urlrequest.urlopen(url, timeout=5.0) as response:
        payload = json.load(response)
    assert isinstance(payload, dict)
    return payload
