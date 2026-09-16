#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
exec bash "$REPO_ROOT/tests/evidence/issue_862/run_storage_vps_capture.sh" "$@"
