#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/benchmarks/run_issue862_storage_vps_capture.sh [OUTPUT_DIR]

Run the #862 storage comparison on an ordinary Linux VPS and retain a
self-describing evidence bundle. The script intentionally refuses GitHub-hosted
Actions so hosted-runner measurements cannot be mislabeled as VPS evidence.

The campaign compares:
  - local filesystem (no resident object-store daemon)
  - RustFS 1.0.0-rc.6
  - Garage v2.3.0
  - SeaweedFS 4.46

Default OUTPUT_DIR: artifacts/issue862-storage-vps
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "#862 VPS evidence capture requires Linux" >&2
  exit 2
fi

case "$(uname -m)" in
  x86_64|amd64|aarch64|arm64) ;;
  *)
    echo "#862 VPS evidence capture requires x86-64 or arm64" >&2
    exit 2
    ;;
esac

if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  echo "refusing to label a GitHub Actions runner as ordinary-VPS evidence" >&2
  exit 2
fi

for command in docker git python3 sha256sum tar curl openssl du find sort xargs id chown; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "missing required command: $command" >&2
    exit 2
  fi
done

if [[ ! -x /usr/bin/time ]]; then
  echo "missing required executable: /usr/bin/time" >&2
  exit 2
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is unavailable or the current user cannot access it" >&2
  exit 2
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
PLATFORM_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
OUTPUT_DIR="${1:-$REPO_ROOT/artifacts/issue862-storage-vps}"
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
if [[ -n "$(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "refusing to mix #862 evidence into non-empty output directory: $OUTPUT_DIR" >&2
  echo "choose an empty directory or remove the previous capture before retrying" >&2
  exit 2
fi

WORK_DIR="$(mktemp -d -t issue862-storage-vps-XXXXXX)"
CONTAINER=""
cleanup() {
  if [[ -n "$CONTAINER" ]]; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

python3 - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12+ is required")
PY

wait_tcp() {
  local host="$1"
  local port="$2"
  local attempts="${3:-120}"
  for _ in $(seq 1 "$attempts"); do
    if python3 - "$host" "$port" <<'PY'
import socket
import sys

try:
    with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=1):
        pass
except OSError:
    raise SystemExit(1)
PY
    then
      sleep 2
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_rustfs() {
  local attempts="${1:-120}"
  for _ in $(seq 1 "$attempts"); do
    if curl -fsS http://127.0.0.1:9000/health/ready >/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

stop_container() {
  if [[ -n "$CONTAINER" ]]; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    CONTAINER=""
  fi
}

sample_active_stats() {
  local container="$1"
  local outfile="$2"
  local pid="$3"
  : > "$outfile"
  while kill -0 "$pid" >/dev/null 2>&1; do
    docker stats --no-stream --format '{{json .}}' "$container" >> "$outfile" || true
    sleep 0.25
  done
  docker stats --no-stream --format '{{json .}}' "$container" >> "$outfile" || true
}

run_s3_workload() {
  local backend="$1"
  local endpoint="$2"
  local bucket="$3"
  local region="$4"
  local create_bucket="$5"
  local outfile="$6"
  local extra=()
  if [[ "$create_bucket" == "true" ]]; then
    extra+=(--create-bucket)
  fi
  python3 "$REPO_ROOT/tests/evidence/issue_862/s3_contract_probe.py" \
    --backend "$backend" \
    --endpoint "$endpoint" \
    --bucket "$bucket" \
    --region "$region" \
    --run-id "vps-$(date -u +%Y%m%dT%H%M%SZ)-${backend}" \
    --payload-bytes $((32 * 1024 * 1024)) \
    --concurrent-payload-bytes $((4 * 1024 * 1024)) \
    --concurrency 8 \
    "${extra[@]}" \
    > "$outfile"
}

capture_backend() {
  local backend="$1"
  local image="$2"
  local endpoint="$3"
  local bucket="$4"
  local region="$5"
  local create_bucket="$6"
  local data_root="$7"

  docker stats --no-stream --format '{{json .}}' "$CONTAINER" \
    > "$OUTPUT_DIR/${backend}-idle-docker-stats.json"

  local workload="$OUTPUT_DIR/${backend}-workload.json"
  run_s3_workload "$backend" "$endpoint" "$bucket" "$region" "$create_bucket" "$workload" &
  local workload_pid=$!
  sample_active_stats "$CONTAINER" "$OUTPUT_DIR/${backend}-active-docker-stats.jsonl" "$workload_pid"
  wait "$workload_pid"

  du -sb "$data_root" > "$OUTPUT_DIR/${backend}-disk-usage.txt"
  {
    echo "backend=$backend"
    echo "image=$image"
    echo "image_id=$(docker image inspect "$image" --format '{{.Id}}')"
    echo "repo_digests=$(docker image inspect "$image" --format '{{json .RepoDigests}}')"
  } > "$OUTPUT_DIR/${backend}-image.txt"
}

RUN_ID="vps-$(date -u +%Y%m%dT%H%M%SZ)"
LOCAL_ROOT="$WORK_DIR/local-filesystem"
mkdir -p "$LOCAL_ROOT"
/usr/bin/time -v -o "$OUTPUT_DIR/local-filesystem-time.txt" \
  python3 "$REPO_ROOT/tests/evidence/issue_862/local_filesystem_benchmark.py" \
  --root "$LOCAL_ROOT" \
  --run-id "$RUN_ID" \
  --payload-bytes $((32 * 1024 * 1024)) \
  --concurrent-payload-bytes $((4 * 1024 * 1024)) \
  --concurrency 8 \
  > "$OUTPUT_DIR/local-filesystem-workload.json"
du -sb "$LOCAL_ROOT" > "$OUTPUT_DIR/local-filesystem-disk-usage.txt"

export AWS_ACCESS_KEY_ID="I862$(openssl rand -hex 10 | tr '[:lower:]' '[:upper:]')"
export AWS_SECRET_ACCESS_KEY="$(openssl rand -hex 32)"

RUSTFS_IMAGE="rustfs/rustfs:1.0.0-rc.6"
RUSTFS_DATA="$WORK_DIR/rustfs-data"
mkdir -p "$RUSTFS_DATA"
if [[ "$(id -u)" -eq 0 ]]; then
  chown -R 10001:10001 "$RUSTFS_DATA"
elif command -v sudo >/dev/null 2>&1; then
  sudo chown -R 10001:10001 "$RUSTFS_DATA"
else
  echo "RustFS data preparation requires root or sudo for chown to uid 10001" >&2
  exit 2
fi
docker pull "$RUSTFS_IMAGE" >/dev/null
CONTAINER="issue862-vps-rustfs"
docker run -d \
  --name "$CONTAINER" \
  -p 127.0.0.1:9000:9000 \
  -e RUSTFS_VOLUMES=/data \
  -e RUSTFS_ADDRESS=0.0.0.0:9000 \
  -e RUSTFS_CONSOLE_ENABLE=false \
  -e RUSTFS_ACCESS_KEY="$AWS_ACCESS_KEY_ID" \
  -e RUSTFS_SECRET_KEY="$AWS_SECRET_ACCESS_KEY" \
  -v "$RUSTFS_DATA:/data" \
  "$RUSTFS_IMAGE" >/dev/null
if ! wait_rustfs; then
  docker logs "$CONTAINER" >&2 || true
  exit 3
fi
capture_backend rustfs "$RUSTFS_IMAGE" http://127.0.0.1:9000 issue-862-vps us-east-1 true "$RUSTFS_DATA"
stop_container

export AWS_ACCESS_KEY_ID="GK$(openssl rand -hex 16)"
export AWS_SECRET_ACCESS_KEY="$(openssl rand -hex 32)"
GARAGE_IMAGE="dxflrs/garage:v2.3.0"
GARAGE_ROOT="$WORK_DIR/garage"
mkdir -p "$GARAGE_ROOT/meta" "$GARAGE_ROOT/data"
GARAGE_RPC_SECRET="$(openssl rand -hex 32)"
GARAGE_ADMIN_TOKEN="$(openssl rand -hex 32)"
GARAGE_METRICS_TOKEN="$(openssl rand -hex 32)"
cat > "$WORK_DIR/garage.toml" <<EOF
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "sqlite"
replication_factor = 1
rpc_bind_addr = "[::]:3901"
rpc_public_addr = "127.0.0.1:3901"
rpc_secret = "$GARAGE_RPC_SECRET"

[s3_api]
s3_region = "garage"
api_bind_addr = "[::]:3900"
root_domain = ".s3.garage.localhost"

[admin]
api_bind_addr = "[::]:3903"
admin_token = "$GARAGE_ADMIN_TOKEN"
metrics_token = "$GARAGE_METRICS_TOKEN"
EOF
docker pull "$GARAGE_IMAGE" >/dev/null
CONTAINER="issue862-vps-garage"
docker run -d \
  --name "$CONTAINER" \
  -p 127.0.0.1:3900:3900 \
  -v "$WORK_DIR/garage.toml:/etc/garage.toml:ro" \
  -v "$GARAGE_ROOT/meta:/var/lib/garage/meta" \
  -v "$GARAGE_ROOT/data:/var/lib/garage/data" \
  -e GARAGE_DEFAULT_ACCESS_KEY="$AWS_ACCESS_KEY_ID" \
  -e GARAGE_DEFAULT_SECRET_KEY="$AWS_SECRET_ACCESS_KEY" \
  -e GARAGE_DEFAULT_BUCKET=issue-862-vps \
  "$GARAGE_IMAGE" /garage server --single-node --default-bucket >/dev/null
if ! wait_tcp 127.0.0.1 3900; then
  docker logs "$CONTAINER" >&2 || true
  exit 3
fi
capture_backend garage "$GARAGE_IMAGE" http://127.0.0.1:3900 issue-862-vps garage false "$GARAGE_ROOT"
stop_container

export AWS_ACCESS_KEY_ID="I862$(openssl rand -hex 10 | tr '[:lower:]' '[:upper:]')"
export AWS_SECRET_ACCESS_KEY="$(openssl rand -hex 32)"
SEAWEED_IMAGE="chrislusf/seaweedfs:4.46"
SEAWEED_DATA="$WORK_DIR/seaweedfs-data"
mkdir -p "$SEAWEED_DATA"
docker pull "$SEAWEED_IMAGE" >/dev/null
CONTAINER="issue862-vps-seaweedfs"
docker run -d \
  --name "$CONTAINER" \
  -p 127.0.0.1:8333:8333 \
  -v "$SEAWEED_DATA:/data" \
  -e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  -e S3_BUCKET=issue-862-vps \
  "$SEAWEED_IMAGE" >/dev/null
if ! wait_tcp 127.0.0.1 8333; then
  docker logs "$CONTAINER" >&2 || true
  exit 3
fi
capture_backend seaweedfs "$SEAWEED_IMAGE" http://127.0.0.1:8333 issue-862-vps us-east-1 false "$SEAWEED_DATA"
stop_container

export PLATFORM_COMMIT OUTPUT_DIR
python3 - <<'PY'
from __future__ import annotations

import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path

output = Path(os.environ["OUTPUT_DIR"])

os_release: dict[str, str] = {}
os_release_path = Path("/etc/os-release")
if os_release_path.exists():
    for line in os_release_path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os_release[key] = value.strip().strip('"')

mem_total_kib = None
for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
    if line.startswith("MemTotal:"):
        mem_total_kib = int(line.split()[1])
        break

cpu_model = None
for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
    if line.lower().startswith("model name") and ":" in line:
        cpu_model = line.split(":", 1)[1].strip()
        break

manifest = {
    "schema_version": 1,
    "issue": 862,
    "evidence_class": "ordinary-vps-reference",
    "captured_at_utc": datetime.now(timezone.utc).isoformat(),
    "platform_commit": os.environ["PLATFORM_COMMIT"],
    "host": {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "cpu_model": cpu_model,
        "mem_total_kib": mem_total_kib,
        "os_release": os_release,
        "python": platform.python_version(),
    },
    "workload": {
        "primary_bytes": 32 * 1024 * 1024,
        "concurrency": 8,
        "concurrent_object_bytes": 4 * 1024 * 1024,
        "canonical_checksum": "sha256",
    },
    "backends": ["local_filesystem", "rustfs", "garage", "seaweedfs"],
}
(output / "storage-vps-evidence-manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

python3 "$REPO_ROOT/scripts/benchmarks/summarize_issue862_storage_vps_capture.py" "$OUTPUT_DIR"

(
  cd "$OUTPUT_DIR"
  find . -maxdepth 1 -type f ! -name 'SHA256SUMS' ! -name '*.tar.gz*' -print0 \
    | sort -z \
    | xargs -0 sha256sum \
    > SHA256SUMS
  tar -czf issue862-storage-vps-evidence.tar.gz \
    --exclude=issue862-storage-vps-evidence.tar.gz \
    --exclude=issue862-storage-vps-evidence.tar.gz.sha256 \
    .
  sha256sum issue862-storage-vps-evidence.tar.gz \
    > issue862-storage-vps-evidence.tar.gz.sha256
)

echo "#862 ordinary-VPS evidence written to: $OUTPUT_DIR"
echo "summary: $OUTPUT_DIR/storage-vps-summary.md"
echo "bundle: $OUTPUT_DIR/issue862-storage-vps-evidence.tar.gz"
echo "bundle SHA-256: $(cat "$OUTPUT_DIR/issue862-storage-vps-evidence.tar.gz.sha256")"
