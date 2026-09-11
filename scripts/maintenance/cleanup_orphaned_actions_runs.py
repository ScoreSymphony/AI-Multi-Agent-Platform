#!/usr/bin/env python3
"""Safely remove completed GitHub Actions runs for deleted workflow files.

The script is a dry-run by default. It compares the workflow path recorded on each
Actions run with workflow files that exist on the repository default branch under
``.github/workflows``. Only completed runs whose workflow path no longer exists
are eligible for deletion.

Examples:
    python scripts/maintenance/cleanup_orphaned_actions_runs.py
    python scripts/maintenance/cleanup_orphaned_actions_runs.py --execute
    python scripts/maintenance/cleanup_orphaned_actions_runs.py --execute --max-delete 4000
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass

WORKFLOW_DIR = ".github/workflows"


@dataclass(frozen=True)
class WorkflowRun:
    run_id: int
    workflow_path: str
    status: str
    conclusion: str
    created_at: str
    name: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Delete completed GitHub Actions runs for workflow files that no longer "
            "exist in .github/workflows on the protected reference branch."
        )
    )
    parser.add_argument(
        "--repo",
        help=(
            "Repository in owner/name form. Defaults to GH_REPO, GITHUB_REPOSITORY, "
            "or the repository reported by `gh repo view`."
        ),
    )
    parser.add_argument(
        "--ref",
        help=(
            "Reference whose .github/workflows directory is authoritative. "
            "Defaults to the repository default branch."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete eligible runs. Without this flag the script is a dry-run.",
    )
    parser.add_argument(
        "--max-delete",
        type=int,
        help=(
            "Maximum number of runs to delete in this invocation. The script only "
            "starts a workflow path when all of its remaining runs fit in the budget."
        ),
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help=(
            "Restrict cleanup to an orphaned workflow path. May be repeated. "
            "Example: --path .github/workflows/tmp-pr7.yml"
        ),
    )
    return parser.parse_args()


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["gh", *args]
    try:
        return subprocess.run(
            command,
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            "GitHub CLI (`gh`) was not found. Install it and run `gh auth login` first."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise SystemExit(f"Command failed: {' '.join(command)}\n{stderr}") from exc


def resolve_repo(explicit_repo: str | None) -> str:
    if explicit_repo:
        return explicit_repo
    for variable in ("GH_REPO", "GITHUB_REPOSITORY"):
        value = os.environ.get(variable)
        if value:
            return value
    result = gh("repo", "view", "--json", "nameWithOwner")
    payload = json.loads(result.stdout)
    return str(payload["nameWithOwner"])


def resolve_ref(repo: str, explicit_ref: str | None) -> str:
    if explicit_ref:
        return explicit_ref
    result = gh(
        "repo",
        "view",
        repo,
        "--json",
        "defaultBranchRef",
        "--jq",
        ".defaultBranchRef.name",
    )
    ref = result.stdout.strip()
    if not ref:
        raise SystemExit(f"Could not determine the default branch for {repo}")
    return ref


def active_workflow_paths(repo: str, ref: str) -> set[str]:
    result = gh(
        "api",
        "--method",
        "GET",
        f"/repos/{repo}/contents/{WORKFLOW_DIR}",
        "-f",
        f"ref={ref}",
        "--jq",
        (
            '.[] | select(.type == "file") | '
            'select(.name | endswith(".yml") or endswith(".yaml")) | .path'
        ),
    )
    paths = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    if not paths:
        raise SystemExit(f"No workflow files found under {WORKFLOW_DIR} at {repo}@{ref}")
    return paths


def canonical_workflow_path(path: str) -> str:
    return path.split("@", 1)[0]


def fetch_workflow_runs(repo: str) -> list[WorkflowRun]:
    result = gh(
        "api",
        "--paginate",
        f"/repos/{repo}/actions/runs?per_page=100",
        "--jq",
        '.workflow_runs[] | [.id, .path, .status, (.conclusion // ""), .created_at, .name] | @tsv',
    )
    runs: list[WorkflowRun] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t", 5)
        if len(fields) != 6:
            raise SystemExit(f"Unexpected workflow-run row: {line!r}")
        run_id, path, status, conclusion, created_at, name = fields
        runs.append(
            WorkflowRun(
                run_id=int(run_id),
                workflow_path=canonical_workflow_path(path),
                status=status,
                conclusion=conclusion,
                created_at=created_at,
                name=name,
            )
        )
    return runs


def orphaned_completed_runs(
    runs: list[WorkflowRun],
    active_paths: set[str],
    requested_paths: set[str],
) -> tuple[dict[str, list[WorkflowRun]], int]:
    grouped: dict[str, list[WorkflowRun]] = defaultdict(list)
    skipped_non_completed = 0
    for run in runs:
        path = run.workflow_path
        if not path.startswith(".github/workflows/"):
            continue
        if path in active_paths:
            continue
        if requested_paths and path not in requested_paths:
            continue
        if run.status != "completed":
            skipped_non_completed += 1
            continue
        grouped[path].append(run)
    return dict(grouped), skipped_non_completed


def print_plan(
    repo: str,
    active_paths: set[str],
    grouped: dict[str, list[WorkflowRun]],
    skipped_non_completed: int,
    max_delete: int | None,
) -> None:
    total = sum(len(runs) for runs in grouped.values())
    print(f"Repository: {repo}")
    print(f"Active workflow files: {len(active_paths)}")
    print(f"Orphaned workflow paths with completed runs: {len(grouped)}")
    print(f"Completed orphaned runs eligible for deletion: {total}")
    if skipped_non_completed:
        print(f"Orphaned runs skipped because they are not completed: {skipped_non_completed}")
    if max_delete is not None:
        print(f"Deletion budget for this invocation: {max_delete}")
    print()
    for path, runs in sorted(grouped.items(), key=lambda item: (len(item[1]), item[0])):
        newest = max(run.created_at for run in runs)
        oldest = min(run.created_at for run in runs)
        names = sorted({run.name for run in runs})
        label = ", ".join(names[:3])
        if len(names) > 3:
            label += f", +{len(names) - 3} more"
        print(f"{len(runs):6d}  {path}")
        print(f"        {oldest} .. {newest}  [{label}]")


def delete_run(repo: str, run: WorkflowRun) -> None:
    result = gh(
        "api",
        "--method",
        "DELETE",
        f"/repos/{repo}/actions/runs/{run.run_id}",
        check=False,
    )
    if result.returncode == 0:
        return
    stderr = (result.stderr or "").strip()
    if "HTTP 404" in stderr or "Not Found" in stderr:
        return
    if "rate limit" in stderr.lower():
        raise SystemExit(
            "GitHub API rate limit reached. Run the same command again after the "
            "rate limit resets; already deleted runs will be skipped automatically."
        )
    raise SystemExit(f"Failed to delete run {run.run_id}: {stderr}")


def execute_cleanup(
    repo: str,
    grouped: dict[str, list[WorkflowRun]],
    max_delete: int | None,
) -> int:
    deleted = 0
    for path, runs in sorted(grouped.items(), key=lambda item: (len(item[1]), item[0])):
        if max_delete is not None:
            remaining = max_delete - deleted
            if remaining <= 0:
                break
            if len(runs) > remaining:
                print(
                    f"Skipping {path}: {len(runs)} runs do not fit in the "
                    f"remaining budget of {remaining}."
                )
                continue
        print(f"Deleting {len(runs)} completed runs for {path} ...")
        for run in sorted(runs, key=lambda item: item.created_at):
            delete_run(repo, run)
            deleted += 1
        print(f"Deleted complete history for {path}.")
    return deleted


def main() -> int:
    args = parse_args()
    if args.max_delete is not None and args.max_delete < 1:
        raise SystemExit("--max-delete must be greater than zero")

    repo = resolve_repo(args.repo)
    ref = resolve_ref(repo, args.ref)
    active_paths = active_workflow_paths(repo, ref)
    requested_paths = {canonical_workflow_path(path) for path in args.path}
    runs = fetch_workflow_runs(repo)
    grouped, skipped_non_completed = orphaned_completed_runs(
        runs,
        active_paths,
        requested_paths,
    )

    print(f"Reference: {ref}")
    print_plan(repo, active_paths, grouped, skipped_non_completed, args.max_delete)
    if not grouped:
        print("Nothing to clean up.")
        return 0
    if not args.execute:
        print()
        print("Dry-run only. Re-run with --execute to delete the listed runs.")
        return 0

    print()
    deleted = execute_cleanup(repo, grouped, args.max_delete)
    print()
    print(f"Deleted {deleted} workflow runs.")
    if deleted < sum(len(runs) for runs in grouped.values()):
        print("Some orphaned workflow histories remain; run the command again to continue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
