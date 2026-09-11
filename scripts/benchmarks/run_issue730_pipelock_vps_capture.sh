#!/usr/bin/env bash
set -euo pipefail

PIPELOCK_REVISION="f7d1816f1a5ad63d501b0c48f36066f836f59022"

usage() {
  cat <<'EOF'
Usage: scripts/benchmarks/run_issue730_pipelock_vps_capture.sh [OUTPUT_DIR]

Run the #730 Pipelock benchmark on an ordinary Linux x86-64 VPS and retain a
self-describing evidence bundle. The script intentionally refuses GitHub-hosted
Actions so hosted-runner measurements cannot be mislabeled as VPS evidence.

Default OUTPUT_DIR: artifacts/issue730-pipelock-vps
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "#730 VPS evidence capture requires Linux" >&2
  exit 2
fi

case "$(uname -m)" in
  x86_64|amd64) ;;
  *)
    echo "#730 VPS evidence capture currently requires x86-64" >&2
    exit 2
    ;;
esac

if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  echo "refusing to label a GitHub Actions runner as ordinary-VPS evidence" >&2
  exit 2
fi

for command in git go make python3 sha256sum tar; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "missing required command: $command" >&2
    exit 2
  fi
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
PLATFORM_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
OUTPUT_DIR="${1:-$REPO_ROOT/artifacts/issue730-pipelock-vps}"
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

WORK_DIR="$(mktemp -d -t issue730-pipelock-vps-XXXXXX)"
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

python3 - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12+ is required")
PY

python3 -m venv "$WORK_DIR/venv"
# shellcheck disable=SC1091
source "$WORK_DIR/venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -e "$REPO_ROOT[dev]"
python -m pip install "websockets==15.0.1"

git clone --quiet --filter=blob:none https://github.com/luckyPipewrench/pipelock.git "$WORK_DIR/pipelock"
git -C "$WORK_DIR/pipelock" checkout --quiet "$PIPELOCK_REVISION"
ACTUAL_REVISION="$(git -C "$WORK_DIR/pipelock" rev-parse HEAD)"
if [[ "$ACTUAL_REVISION" != "$PIPELOCK_REVISION" ]]; then
  echo "Pipelock checkout mismatch: $ACTUAL_REVISION" >&2
  exit 3
fi

make -C "$WORK_DIR/pipelock" build
PIPELOCK_BIN="$WORK_DIR/pipelock/pipelock"
BUILDINFO="$OUTPUT_DIR/pipelock-buildinfo.txt"
go version -m "$PIPELOCK_BIN" | tee "$BUILDINFO"
if grep -Eq 'tags=.*enterprise' "$BUILDINFO"; then
  echo "candidate binary contains Enterprise build tag" >&2
  exit 3
fi

BENCHMARK_JSON="$OUTPUT_DIR/pipelock-performance-vps.json"
python "$REPO_ROOT/scripts/benchmarks/issue730_pipelock_benchmark.py" \
  --pipelock-bin "$PIPELOCK_BIN" \
  --config "$REPO_ROOT/tests/fixtures/pipelock_websocket_audit.yaml" \
  --output "$BENCHMARK_JSON" \
  --environment-label ordinary-vps-reference \
  --iterations 50 \
  --warmups 5 \
  --mcp-iterations 10

PIPELOCK_SHA256="$(sha256sum "$PIPELOCK_BIN" | awk '{print $1}')"
CONFIG_PATH="$REPO_ROOT/tests/fixtures/pipelock_websocket_audit.yaml"
CONFIG_SHA256="$(sha256sum "$CONFIG_PATH" | awk '{print $1}')"
BENCHMARK_SHA256="$(sha256sum "$BENCHMARK_JSON" | awk '{print $1}')"
export PIPELOCK_REVISION PLATFORM_COMMIT PIPELOCK_SHA256 CONFIG_SHA256 BENCHMARK_SHA256
export BENCHMARK_JSON OUTPUT_DIR

python - <<'PY'
from __future__ import annotations

import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

benchmark_path = Path(os.environ["BENCHMARK_JSON"])
benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
if benchmark.get("issue") != 730:
    raise SystemExit("benchmark issue identity mismatch")
if benchmark.get("pipelock_revision") != os.environ["PIPELOCK_REVISION"]:
    raise SystemExit("benchmark Pipelock revision mismatch")
if benchmark.get("environment", {}).get("label") != "ordinary-vps-reference":
    raise SystemExit("benchmark environment label mismatch")
if benchmark.get("environment", {}).get("system") != "Linux":
    raise SystemExit("benchmark did not report Linux")
if benchmark.get("environment", {}).get("machine") not in {"x86_64", "amd64"}:
    raise SystemExit("benchmark did not report x86-64")

os_release: dict[str, str] = {}
os_release_path = Path("/etc/os-release")
if os_release_path.exists():
    for line in os_release_path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os_release[key] = value.strip().strip('"')

mem_total_kib = None
meminfo_path = Path("/proc/meminfo")
if meminfo_path.exists():
    for line in meminfo_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            mem_total_kib = int(line.split()[1])
            break

cpu_model = None
cpuinfo_path = Path("/proc/cpuinfo")
if cpuinfo_path.exists():
    for line in cpuinfo_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lower().startswith("model name") and ":" in line:
            cpu_model = line.split(":", 1)[1].strip()
            break

manifest = {
    "schema_version": 1,
    "issue": 730,
    "evidence_class": "ordinary-vps-reference",
    "captured_at_utc": datetime.now(timezone.utc).isoformat(),
    "platform_commit": os.environ["PLATFORM_COMMIT"],
    "pipelock_revision": os.environ["PIPELOCK_REVISION"],
    "hashes": {
        "pipelock_binary_sha256": os.environ["PIPELOCK_SHA256"],
        "benchmark_config_sha256": os.environ["CONFIG_SHA256"],
        "benchmark_json_sha256": os.environ["BENCHMARK_SHA256"],
    },
    "host": {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "cpu_model": cpu_model,
        "mem_total_kib": mem_total_kib,
        "os_release": os_release,
        "python": platform.python_version(),
        "go": subprocess.run(
            ["go", "version"], check=True, capture_output=True, text=True
        ).stdout.strip(),
    },
    "classification": benchmark.get("classification"),
}
manifest_path = Path(os.environ["OUTPUT_DIR"]) / "pipelock-vps-evidence-manifest.json"
manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

(
  cd "$OUTPUT_DIR"
  sha256sum \
    pipelock-performance-vps.json \
    pipelock-buildinfo.txt \
    pipelock-vps-evidence-manifest.json \
    > SHA256SUMS
  tar -czf issue730-pipelock-vps-evidence.tar.gz \
    pipelock-performance-vps.json \
    pipelock-buildinfo.txt \
    pipelock-vps-evidence-manifest.json \
    SHA256SUMS
  sha256sum issue730-pipelock-vps-evidence.tar.gz \
    > issue730-pipelock-vps-evidence.tar.gz.sha256
)

echo "#730 ordinary-VPS evidence written to: $OUTPUT_DIR"
echo "bundle: $OUTPUT_DIR/issue730-pipelock-vps-evidence.tar.gz"
echo "bundle SHA-256: $(cat "$OUTPUT_DIR/issue730-pipelock-vps-evidence.tar.gz.sha256")"
