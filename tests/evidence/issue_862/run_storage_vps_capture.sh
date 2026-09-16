#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HARNESS="$REPO_ROOT/tests/evidence/issue_862/storage_vps_capture_harness.sh"
PATCHED_HARNESS="$(mktemp -t storage-vps-capture-XXXXXX.sh)"
trap 'rm -f "$PATCHED_HARNESS"' EXIT

sed \
  's#scripts/benchmarks/summarize_issue862_storage_vps_capture.py#scripts/benchmarks/summarize_storage_vps_capture.py#g' \
  "$HARNESS" > "$PATCHED_HARNESS"
chmod +x "$PATCHED_HARNESS"
bash "$PATCHED_HARNESS" "$@"
