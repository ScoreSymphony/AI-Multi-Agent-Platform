#!/usr/bin/env bash
set -euo pipefail

network="bifrost-rebinding-net"
mock="bifrost-rebinding-mock"
sentinel="bifrost-rebinding-sentinel"
dns="bifrost-rebinding-dns"
gateway="bifrost-rebinding-gateway"
data_dir="$GITHUB_WORKSPACE/.bifrost-rebinding"
artifact="$GITHUB_WORKSPACE/artifacts/bifrost-dns-rebinding-runtime.json"

cleanup() {
  status=$?
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    echo "--- rebinding DNS ---"
    docker logs "$dns" || true
    echo "--- sentinel ---"
    docker logs "$sentinel" || true
    echo "--- upstream mock ---"
    docker logs "$mock" || true
    echo "--- Bifrost ---"
    docker logs "$gateway" || true
    echo "--- Bifrost resolv.conf ---"
    docker exec "$gateway" cat /etc/resolv.conf || true
    echo "--- network ---"
    docker network inspect "$network" || true
  fi
  docker rm -f "$gateway" "$dns" "$sentinel" "$mock" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

docker network create \
  --driver bridge \
  --subnet 203.0.113.0/24 \
  "$network"
docker pull python:3.12-slim
docker pull maximhq/bifrost:v2.1.1

docker run -d \
  --name "$mock" \
  --network "$network" \
  --ip 203.0.113.20 \
  -v "$GITHUB_WORKSPACE/scripts/ci/issue859_mock_openai.py:/fixture.py:ro" \
  python:3.12-slim \
  python /fixture.py --host 0.0.0.0 --port 18000

docker run -d \
  --name "$sentinel" \
  --network "$network" \
  --ip 203.0.113.10 \
  -p 127.0.0.1:18001:18001 \
  -v "$GITHUB_WORKSPACE/scripts/ci/issue859_ssrf_sentinel.py:/fixture.py:ro" \
  python:3.12-slim \
  python /fixture.py \
    --host 0.0.0.0 \
    --port 18001 \
    --rebinding-location http://issue859-rebind.test:18003/ssrf-sentinel.txt

docker run -d \
  --name "$dns" \
  --network "$network" \
  --ip 203.0.113.53 \
  -p 127.0.0.1:18002:18002 \
  -v "$GITHUB_WORKSPACE/scripts/ci/issue859_rebinding_dns.py:/fixture.py:ro" \
  python:3.12-slim \
  python /fixture.py \
    --dns-host 0.0.0.0 \
    --dns-port 53 \
    --control-host 0.0.0.0 \
    --control-port 18002 \
    --hostname issue859-rebind.test \
    --first-ip 203.0.113.10 \
    --rebound-ip 127.0.0.1

python - <<'PY'
import json
import time
import urllib.request

for url in (
    "http://127.0.0.1:18001/healthz",
    "http://127.0.0.1:18002/healthz",
):
    for _ in range(60):
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                payload = json.load(response)
            if payload.get("healthy") is True:
                break
        except Exception:
            time.sleep(0.25)
    else:
        raise SystemExit(f"controlled Bifrost rebinding fixture did not become healthy: {url}")
PY

mkdir -p "$data_dir" "$GITHUB_WORKSPACE/artifacts"
chmod 777 "$data_dir"
cat > "$data_dir/config.json" <<'JSON'
{
  "encryption_key": "env.BIFROST_ENCRYPTION_KEY",
  "client": {
    "drop_excess_requests": false,
    "enable_logging": false
  },
  "providers": {
    "bifrost-local": {
      "keys": [
        {
          "name": "fixture-key",
          "value": "env.BIFROST_REBINDING_PROVIDER_KEY",
          "models": ["fixture-model"],
          "weight": 1.0
        }
      ],
      "network_config": {
        "base_url": "http://203.0.113.20:18000",
        "default_request_timeout_in_seconds": 5,
        "max_retries": 0
      },
      "custom_provider_config": {
        "base_provider_type": "openai",
        "allowed_requests": {
          "list_models": true,
          "chat_completion": true,
          "chat_completion_stream": true
        },
        "request_path_overrides": {
          "chat_completion": "/v1/chat/completions",
          "chat_completion_stream": "/v1/chat/completions"
        }
      }
    },
    "openai": {
      "keys": [
        {
          "name": "native-openai-key",
          "value": "env.BIFROST_REBINDING_NATIVE_OPENAI_KEY",
          "models": ["fixture-model"],
          "weight": 1.0
        }
      ],
      "network_config": {
        "base_url": "http://203.0.113.20:18000/v1",
        "default_request_timeout_in_seconds": 5,
        "max_retries": 0
      }
    }
  },
  "config_store": {
    "enabled": false
  }
}
JSON

docker run -d \
  --name "$gateway" \
  --network "$network" \
  --ip 203.0.113.30 \
  --dns 203.0.113.53 \
  -p 127.0.0.1:18080:8080 \
  -v "$data_dir:/app/data" \
  -e APP_HOST=0.0.0.0 \
  -e BIFROST_ENCRYPTION_KEY=0123456789abcdef0123456789abcdef \
  -e BIFROST_REBINDING_PROVIDER_KEY=ci-fixture-provider-key \
  -e BIFROST_REBINDING_NATIVE_OPENAI_KEY=ci-fixture-native-openai-key \
  maximhq/bifrost:v2.1.1

python - <<'PY'
import json
import time
import urllib.error
import urllib.request

payload = json.dumps({
    "model": "bifrost-local/fixture-model",
    "messages": [{"role": "user", "content": "ready"}],
}).encode("utf-8")
for _ in range(120):
    request = urllib.request.Request(
        "http://127.0.0.1:18080/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            body = json.load(response)
        if response.status == 200 and body.get("choices"):
            break
    except (OSError, urllib.error.HTTPError):
        time.sleep(0.5)
else:
    raise SystemExit("Bifrost DNS-rebinding endpoint did not become healthy")
PY

BIFROST_EVAL_BIFROST_BASE_URL=http://127.0.0.1:18080/v1 \
BIFROST_EVAL_BIFROST_NATIVE_OPENAI_MODEL=openai/fixture-model \
BIFROST_EVAL_SSRF_REBINDING_URL=http://issue859-rebind.test:18001/redirect-to-rebound-host \
BIFROST_EVAL_REBINDING_DNS_CONTROL_URL=http://127.0.0.1:18002 \
BIFROST_EVAL_SSRF_SENTINEL_CONTROL_URL=http://127.0.0.1:18001/control \
pytest -q tests/integration/platform/test_bifrost_dns_rebinding.py -m integration

python - <<'PY'
import json
import pathlib
import urllib.request


def get(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=3) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise SystemExit(f"unexpected JSON payload from {url}")
    return payload


evidence = {
    "bifrost_version": "v2.1.1",
    "network": "isolated Docker bridge 203.0.113.0/24",
    "hostname": "issue859-rebind.test",
    "entry_url": "http://issue859-rebind.test:18001/redirect-to-rebound-host",
    "redirect_url": "http://issue859-rebind.test:18003/ssrf-sentinel.txt",
    "first_dns_answer": "203.0.113.10",
    "rebound_dns_answer": "127.0.0.1",
    "dns": get("http://127.0.0.1:18002/stats"),
    "sentinel": get("http://127.0.0.1:18001/control/stats"),
    "expected_contract": (
        "public first dial reaches controlled redirect; redirect forces a new dial; "
        "new dial re-resolves and blocks rebound loopback before connection"
    ),
}
path = pathlib.Path("artifacts/bifrost-dns-rebinding-runtime.json")
path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

test -s "$artifact"
