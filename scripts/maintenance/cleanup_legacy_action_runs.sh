#!/usr/bin/env bash
set -euo pipefail

REPO="${GITHUB_REPOSITORY:-ScoreSymphony/AI-Multi-Agent-Platform}"
API_VERSION="2022-11-28"
MODE="${1:-}"
OUT_DIR="${2:-artifacts/legacy-action-cleanup}"
MAX_SECONDS="${MAX_CLEANUP_SECONDS:-10800}"

mkdir -p "$OUT_DIR"

collect_protected_paths() {
  local output="$1"
  local tmp head_repo head_sha
  tmp="$(mktemp)"
  : >"$tmp"

  gh api \
    -H "X-GitHub-Api-Version: ${API_VERSION}" \
    "/repos/${REPO}/contents/.github/workflows?ref=main" \
    --jq '.[] | select(.type == "file") | .path' >>"$tmp"

  while IFS=$'\t' read -r head_repo head_sha; do
    [[ -n "$head_repo" && -n "$head_sha" ]] || continue
    gh api \
      -H "X-GitHub-Api-Version: ${API_VERSION}" \
      "/repos/${head_repo}/contents/.github/workflows?ref=${head_sha}" \
      --jq '.[] | select(.type == "file") | .path' >>"$tmp" 2>/dev/null || true
  done < <(
    gh api --paginate \
      -H "X-GitHub-Api-Version: ${API_VERSION}" \
      "/repos/${REPO}/pulls?state=open&per_page=100" \
      --jq '.[] | select(.head.repo != null) | [.head.repo.full_name, .head.sha] | @tsv'
  )

  sort -u "$tmp" >"$output"
  rm -f "$tmp"
}

plan_cleanup() {
  local tmp_dir runs_file protected_file
  tmp_dir="$(mktemp -d)"
  runs_file="${tmp_dir}/runs.tsv"
  protected_file="${OUT_DIR}/protected-workflow-paths.txt"

  collect_protected_paths "$protected_file"

  gh api --paginate \
    -H "X-GitHub-Api-Version: ${API_VERSION}" \
    "/repos/${REPO}/actions/runs?per_page=100" \
    --jq '.workflow_runs[] | select(.status == "completed") | [.id, .workflow_id, .path, .name, .created_at, (.conclusion // "")] | @tsv' \
    >"$runs_file"

  python - "$protected_file" "$runs_file" "$OUT_DIR" <<'PY'
from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

protected_path = Path(sys.argv[1])
runs_path = Path(sys.argv[2])
out_dir = Path(sys.argv[3])

protected = {
    line.strip()
    for line in protected_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
}

candidates: list[tuple[str, str, str, str, str, str]] = []
workflow_counts: Counter[tuple[str, str, str]] = Counter()
total_completed = 0

for raw in runs_path.read_text(encoding="utf-8").splitlines():
    if not raw.strip():
        continue
    parts = raw.split("\t", 5)
    if len(parts) != 6:
        continue
    run_id, workflow_id, path, name, created_at, conclusion = parts
    total_completed += 1
    if not path.startswith(".github/workflows/"):
        continue
    if not path.endswith((".yml", ".yaml")):
        continue
    if path in protected:
        continue
    row = (run_id, workflow_id, path, name, created_at, conclusion)
    candidates.append(row)
    workflow_counts[(workflow_id, path, name)] += 1

candidates.sort(key=lambda row: (row[2], int(row[0])))

(out_dir / "stale-runs.tsv").write_text(
    "".join("\t".join(row) + "\n" for row in candidates),
    encoding="utf-8",
)

workflow_lines = []
for (workflow_id, path, name), count in sorted(
    workflow_counts.items(), key=lambda item: (-item[1], item[0][1])
):
    workflow_lines.append(f"{workflow_id}\t{path}\t{name}\t{count}\n")
(out_dir / "stale-workflows.tsv").write_text("".join(workflow_lines), encoding="utf-8")

summary = [
    "# Legacy Actions cleanup plan",
    "",
    f"- Completed workflow runs scanned: {total_completed}",
    f"- Protected workflow paths (main + open PR heads): {len(protected)}",
    f"- Stale workflow identities: {len(workflow_counts)}",
    f"- Candidate runs to delete: {len(candidates)}",
    "",
    "Only completed runs whose workflow path is absent from both `main` and every open PR head are candidates.",
]
(out_dir / "plan-summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")

print(f"candidate_runs={len(candidates)}")
print(f"stale_workflows={len(workflow_counts)}")
PY

  rm -rf "$tmp_dir"
}

rate_guard() {
  local remaining reset now sleep_for
  read -r remaining reset < <(
    gh api rate_limit --jq '.resources.core | [.remaining, .reset] | @tsv'
  )
  if (( remaining < 300 )); then
    now="$(date +%s)"
    sleep_for=$(( reset - now + 10 ))
    if (( sleep_for > 0 )); then
      echo "REST API budget low (${remaining} remaining); sleeping ${sleep_for}s until reset."
      sleep "$sleep_for"
    fi
  fi
}

apply_cleanup() {
  local plan_file protected_now summary_file continuation_file
  local started total deleted skipped missing failed processed remaining continuation
  local run_id workflow_id path name created_at conclusion current_path delete_output delete_rc

  plan_file="${OUT_DIR}/stale-runs.tsv"
  protected_now="${OUT_DIR}/protected-workflow-paths-now.txt"
  summary_file="${OUT_DIR}/apply-summary.md"
  continuation_file="${OUT_DIR}/continuation.env"

  [[ -f "$plan_file" ]] || {
    echo "Missing cleanup plan: $plan_file" >&2
    exit 2
  }

  collect_protected_paths "$protected_now"

  started="$(date +%s)"
  total="$(wc -l <"$plan_file" | tr -d ' ')"
  deleted=0
  skipped=0
  missing=0
  failed=0
  processed=0
  current_path=""

  while IFS=$'\t' read -r run_id workflow_id path name created_at conclusion; do
    [[ -n "$run_id" ]] || continue

    if (( $(date +%s) - started >= MAX_SECONDS )); then
      echo "Cleanup time budget reached after ${processed}/${total} planned runs."
      break
    fi

    # The plan is sorted by workflow path. Refresh the protection set whenever
    # processing moves to a new path, so a workflow added after planning is safe
    # without spending API requests on every individual historical run.
    if [[ "$path" != "$current_path" ]]; then
      current_path="$path"
      collect_protected_paths "$protected_now"
    fi

    if grep -Fqx "$path" "$protected_now"; then
      skipped=$(( skipped + 1 ))
      processed=$(( processed + 1 ))
      continue
    fi

    if (( deleted % 50 == 0 )); then
      rate_guard
    fi

    set +e
    delete_output="$(
      gh api \
        --method DELETE \
        -H "X-GitHub-Api-Version: ${API_VERSION}" \
        "/repos/${REPO}/actions/runs/${run_id}" \
        2>&1
    )"
    delete_rc=$?
    set -e

    if (( delete_rc == 0 )); then
      deleted=$(( deleted + 1 ))
    elif grep -q "HTTP 404" <<<"$delete_output"; then
      missing=$(( missing + 1 ))
    else
      failed=$(( failed + 1 ))
      echo "Failed to delete run ${run_id} (${path}): ${delete_output}" >&2
    fi

    processed=$(( processed + 1 ))

    if (( processed % 100 == 0 )); then
      echo "Processed ${processed}/${total}: deleted=${deleted}, skipped=${skipped}, missing=${missing}, failed=${failed}"
    fi

    sleep 0.35
  done <"$plan_file"

  remaining=$(( total - processed + failed ))
  continuation="false"
  if (( remaining > 0 )); then
    continuation="true"
  fi

  cat >"$summary_file" <<EOF
# Legacy Actions cleanup result

- Planned candidate runs: ${total}
- Processed this pass: ${processed}
- Deleted: ${deleted}
- Skipped because the workflow path became protected: ${skipped}
- Already missing: ${missing}
- Delete failures: ${failed}
- Estimated remaining for a follow-up pass: ${remaining}
- Continuation required: ${continuation}
EOF

  printf 'CONTINUATION_NEEDED=%s\n' "$continuation" >"$continuation_file"
  cat "$summary_file"
}

case "$MODE" in
  plan)
    plan_cleanup
    ;;
  apply)
    apply_cleanup
    ;;
  *)
    echo "Usage: $0 {plan|apply} [output-directory]" >&2
    exit 2
    ;;
esac
