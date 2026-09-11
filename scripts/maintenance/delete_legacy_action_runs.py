#!/usr/bin/env python3
"""Delete completed runs for retired GitHub Actions workflows.

The script discovers workflow runs whose ``path`` no longer exists on ``main``
(and, by default, on any open pull-request head), then deletes those runs via
GitHub's REST endpoint:

    DELETE /repos/{owner}/{repo}/actions/runs/{run_id}

Dry-run is the default. Pass ``--execute`` to actually delete runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
PER_PAGE = 100


@dataclass(frozen=True)
class Run:
    run_id: int
    path: str
    name: str
    created_at: str


class GitHubClient:
    def __init__(self, token: str, repo: str) -> None:
        self.token = token
        self.repo = repo
        self._print_lock = threading.Lock()

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str | int] | None = None,
        retries: int = 8,
    ) -> HTTPResponse:
        url = f"{API_ROOT}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "scoresymphony-actions-cleanup",
        }

        for attempt in range(retries + 1):
            request = Request(url, method=method, headers=headers)
            try:
                return urlopen(request, timeout=60)  # noqa: S310
            except HTTPError as exc:
                if exc.code == 404 and method == "DELETE":
                    return exc
                if exc.code not in {403, 429, 502, 503, 504} or attempt >= retries:
                    raise

                retry_after = exc.headers.get("Retry-After")
                reset = exc.headers.get("X-RateLimit-Reset")
                if retry_after:
                    delay = max(1, int(retry_after))
                elif exc.code == 403 and reset:
                    delay = max(1, int(reset) - int(time.time()) + 2)
                else:
                    delay = min(60, 2**attempt)

                with self._print_lock:
                    print(
                        f"GitHub API returned {exc.code}; retrying in {delay}s...",
                        file=sys.stderr,
                    )
                time.sleep(delay)
            except URLError:
                if attempt >= retries:
                    raise
                time.sleep(min(30, 2**attempt))

        raise RuntimeError("request retry loop exhausted")

    def json(
        self,
        path: str,
        *,
        query: dict[str, str | int] | None = None,
    ) -> Any:
        with self._request("GET", path, query=query) as response:
            return json.load(response)

    def delete_run(self, run_id: int) -> str:
        endpoint = f"/repos/{self.repo}/actions/runs/{run_id}"
        response = self._request("DELETE", endpoint)
        status = response.status
        response.close()
        if status == 204:
            return "deleted"
        if status == 404:
            return "missing"
        raise RuntimeError(f"unexpected DELETE status {status} for run {run_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete completed runs belonging to retired GitHub Actions workflows."
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", "ScoreSymphony/AI-Multi-Agent-Platform"),
        help="Repository in owner/name form.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"),
        help="GitHub token. Defaults to GH_TOKEN or GITHUB_TOKEN.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel DELETE requests (default: 8).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete runs. Without this flag the script only prints the plan.",
    )
    parser.add_argument(
        "--no-protect-open-prs",
        action="store_true",
        help="Protect only workflows on main, not workflows present solely on open PR heads.",
    )
    return parser.parse_args()


def workflow_paths_for_ref(client: GitHubClient, repo: str, ref: str) -> set[str]:
    try:
        entries = client.json(
            f"/repos/{repo}/contents/.github/workflows",
            query={"ref": ref},
        )
    except HTTPError as exc:
        if exc.code == 404:
            return set()
        raise

    if not isinstance(entries, list):
        return set()
    return {
        str(item["path"])
        for item in entries
        if item.get("type") == "file" and str(item.get("path", "")).endswith((".yml", ".yaml"))
    }


def collect_protected_paths(client: GitHubClient, protect_open_prs: bool) -> set[str]:
    protected = workflow_paths_for_ref(client, client.repo, "main")
    if not protect_open_prs:
        return protected

    page = 1
    while True:
        pulls = client.json(
            f"/repos/{client.repo}/pulls",
            query={"state": "open", "per_page": PER_PAGE, "page": page},
        )
        if not pulls:
            break
        for pull in pulls:
            head = pull.get("head") or {}
            head_repo = head.get("repo") or {}
            full_name = head_repo.get("full_name")
            sha = head.get("sha")
            if full_name and sha:
                protected.update(workflow_paths_for_ref(client, str(full_name), str(sha)))
        if len(pulls) < PER_PAGE:
            break
        page += 1
    return protected


def collect_stale_completed_runs(client: GitHubClient, protected: set[str]) -> list[Run]:
    stale: list[Run] = []
    page = 1
    scanned = 0

    while True:
        payload = client.json(
            f"/repos/{client.repo}/actions/runs",
            query={"per_page": PER_PAGE, "page": page},
        )
        runs = payload.get("workflow_runs", [])
        if not runs:
            break

        for raw in runs:
            scanned += 1
            path = str(raw.get("path") or "")
            if raw.get("status") != "completed":
                continue
            if not path.startswith(".github/workflows/"):
                continue
            if not path.endswith((".yml", ".yaml")):
                continue
            if path in protected:
                continue
            stale.append(
                Run(
                    run_id=int(raw["id"]),
                    path=path,
                    name=str(raw.get("name") or ""),
                    created_at=str(raw.get("created_at") or ""),
                )
            )

        if page % 25 == 0:
            print(f"Scanned {scanned} runs; found {len(stale)} stale completed runs...")
        if len(runs) < PER_PAGE:
            break
        page += 1

    counts = Counter(run.path for run in stale)
    stale.sort(key=lambda run: (counts[run.path], run.path, run.run_id))
    return stale


def print_plan(runs: list[Run]) -> None:
    counts = Counter(run.path for run in runs)
    print(f"Stale completed runs: {len(runs)}")
    print(f"Retired workflow paths: {len(counts)}")
    print("Deletion order: smallest complete workflow histories first")
    print()
    for path, count in sorted(counts.items(), key=lambda item: (item[1], item[0])):
        print(f"{count:6d}  {path}")


def execute_deletes(client: GitHubClient, runs: list[Run], workers: int) -> int:
    if workers < 1:
        raise ValueError("--workers must be at least 1")

    deleted = 0
    missing = 0
    failed = 0
    total = len(runs)
    lock = threading.Lock()

    def delete_one(run: Run) -> tuple[Run, str, str | None]:
        try:
            return run, client.delete_run(run.run_id), None
        except Exception as exc:  # noqa: BLE001
            return run, "failed", str(exc)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(delete_one, run) for run in runs]
        for processed, future in enumerate(as_completed(futures), start=1):
            run, result, error = future.result()
            with lock:
                if result == "deleted":
                    deleted += 1
                elif result == "missing":
                    missing += 1
                else:
                    failed += 1
                    print(
                        f"FAILED run {run.run_id} ({run.path}): {error}",
                        file=sys.stderr,
                    )

                if processed % 100 == 0 or processed == total:
                    print(
                        f"Processed {processed}/{total}: "
                        f"deleted={deleted}, missing={missing}, failed={failed}"
                    )

    return 1 if failed else 0


def main() -> int:
    args = parse_args()
    if not args.token:
        print(
            "Missing token. Set GH_TOKEN/GITHUB_TOKEN or pass --token.",
            file=sys.stderr,
        )
        return 2

    client = GitHubClient(args.token, args.repo)
    protected = collect_protected_paths(client, not args.no_protect_open_prs)
    print(f"Protected workflow paths: {len(protected)}")

    runs = collect_stale_completed_runs(client, protected)
    print_plan(runs)

    if not args.execute:
        print("\nDry-run only. Re-run with --execute to delete these runs.")
        return 0

    if not runs:
        return 0

    print(f"\nDeleting via DELETE /repos/{args.repo}/actions/runs/{{run_id}} ...")
    return execute_deletes(client, runs, args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
