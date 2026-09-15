from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
JOBS_MARKER = "\njobs:\n"
PATH_BLOCK = re.compile(
    r"(?ms)(^  pull_request:\n(?:    branches:.*\n)?    paths:\n)(?P<body>(?:      - .+\n)+)"
)


def read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def write(name: str, content: str) -> None:
    if not content.endswith("\n"):
        content += "\n"
    (WORKFLOWS / name).write_text(content, encoding="utf-8")


def split_jobs(content: str) -> tuple[str, str]:
    if content.count(JOBS_MARKER) != 1:
        raise SystemExit("workflow must contain exactly one top-level jobs marker")
    header, jobs = content.split(JOBS_MARKER, 1)
    return header + JOBS_MARKER, jobs


def job_ids(jobs: str) -> set[str]:
    return set(re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", jobs))


def append_jobs(target_content: str, source_contents: list[str]) -> str:
    target_header, target_jobs = split_jobs(target_content)
    merged_jobs = target_jobs.rstrip()
    ids = job_ids(target_jobs)
    for source_content in source_contents:
        _, source_jobs = split_jobs(source_content)
        source_ids = job_ids(source_jobs)
        collisions = ids & source_ids
        if collisions:
            raise SystemExit(f"job id collision: {sorted(collisions)}")
        ids |= source_ids
        merged_jobs += "\n\n" + source_jobs.strip()
    return target_header + merged_jobs + "\n"


def path_value(line: str) -> str:
    value = line.strip()[2:].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\"', "'"}:
        value = value[1:-1]
    return value


def merge_pull_request_paths(
    header: str,
    source_headers: list[str],
    *,
    drop_paths: set[str] | None = None,
    add_paths: list[str] | None = None,
) -> str:
    drop_paths = drop_paths or set()
    add_paths = add_paths or []
    match = PATH_BLOCK.search(header)
    if match is None:
        raise SystemExit("target workflow has no simple pull_request paths block")

    target_lines = match.group("body").splitlines(keepends=True)
    kept: list[str] = []
    seen: set[str] = set()
    for line in target_lines:
        value = path_value(line)
        if value in drop_paths:
            continue
        if value not in seen:
            kept.append(line)
            seen.add(value)

    for source_header in source_headers:
        source_match = PATH_BLOCK.search(source_header)
        if source_match is None:
            raise SystemExit("source workflow has no simple pull_request paths block")
        for line in source_match.group("body").splitlines(keepends=True):
            value = path_value(line)
            if value in drop_paths or value in seen:
                continue
            kept.append(line)
            seen.add(value)

    for value in add_paths:
        if value not in seen:
            kept.append(f'      - "{value}"\n')
            seen.add(value)

    replacement = "".join(kept)
    return header[: match.start("body")] + replacement + header[match.end("body") :]


def merge_benchmark() -> None:
    target = "benchmark-smoke.yml"
    source = "performance-extended-smoke.yml"
    target_content = read(target)
    source_content = read(source)
    target_header, _ = split_jobs(target_content)
    source_header, _ = split_jobs(source_content)
    normalized_target = target_header.replace("name: Benchmark smoke", "name: Benchmark and performance smoke", 1)
    normalized_source = source_header.replace("name: Performance extended smoke", "name: Benchmark and performance smoke", 1)
    if normalized_target != normalized_source:
        raise SystemExit("benchmark workflow triggers diverged; refusing unsafe merge")
    write(target, append_jobs(target_content, [source_content]).replace("name: Benchmark smoke", "name: Benchmark and performance smoke", 1))
    (WORKFLOWS / source).unlink()


def merge_pipelock() -> None:
    target = "pipelock.yml"
    source = "pipelock-candidate-compat.yml"
    target_content = read(target)
    source_content = read(source)
    target_header, target_jobs = split_jobs(target_content)
    source_header, source_jobs = split_jobs(source_content)
    target_header = merge_pull_request_paths(
        target_header,
        [source_header],
        drop_paths={f".github/workflows/{source}"},
    )
    collisions = job_ids(target_jobs) & job_ids(source_jobs)
    if collisions:
        raise SystemExit(f"Pipelock job id collision: {sorted(collisions)}")
    write(
        target,
        target_header.replace("name: Pipelock validation suite", "name: Pipelock validation and compatibility", 1)
        + target_jobs.rstrip()
        + "\n\n"
        + source_jobs.strip()
        + "\n",
    )
    (WORKFLOWS / source).unlink()


def merge_bifrost() -> None:
    security = "issue859-bifrost-security.yml"
    performance = "issue859-bifrost-performance.yml"
    dns = "issue859-bifrost-dns-rebinding.yml"
    output = "bifrost-evaluation.yml"

    security_content = read(security)
    performance_content = read(performance)
    dns_content = read(dns)
    security_header, security_jobs = split_jobs(security_content)
    performance_header, performance_jobs = split_jobs(performance_content)
    dns_header, dns_jobs = split_jobs(dns_content)

    old_paths = {
        f".github/workflows/{security}",
        f".github/workflows/{performance}",
        f".github/workflows/{dns}",
    }
    merged_header = merge_pull_request_paths(
        security_header,
        [performance_header, dns_header],
        drop_paths=old_paths,
        add_paths=[f".github/workflows/{output}"],
    ).replace("name: Bifrost evaluation security", "name: Bifrost evaluation", 1)

    ids = job_ids(security_jobs)
    for extra_jobs in (performance_jobs, dns_jobs):
        collisions = ids & job_ids(extra_jobs)
        if collisions:
            raise SystemExit(f"Bifrost job id collision: {sorted(collisions)}")
        ids |= job_ids(extra_jobs)

    write(
        output,
        merged_header
        + security_jobs.rstrip()
        + "\n\n"
        + performance_jobs.strip()
        + "\n\n"
        + dns_jobs.strip()
        + "\n",
    )
    for name in (security, performance, dns):
        (WORKFLOWS / name).unlink()


def main() -> None:
    merge_benchmark()
    merge_pipelock()
    merge_bifrost()

    helper_workflow = WORKFLOWS / "workflow-consolidation-helper.yml"
    if helper_workflow.exists():
        helper_workflow.unlink()
    Path(__file__).unlink()

    workflow_files = sorted(
        path.name for path in WORKFLOWS.iterdir() if path.suffix.lower() in {".yml", ".yaml"}
    )
    if len(workflow_files) != 13:
        raise SystemExit(f"expected 13 workflows after consolidation, found {len(workflow_files)}: {workflow_files}")
    if any(name.startswith("issue-862-") or name.startswith("issue859-") for name in workflow_files):
        raise SystemExit(f"issue-specific workflow survived consolidation: {workflow_files}")
    required = {
        "ci.yml",
        "codeql.yml",
        "governance.yml",
        "repository-quality.yml",
        "benchmark-smoke.yml",
        "pipelock.yml",
        "bifrost-evaluation.yml",
        "ha-postgres.yml",
    }
    missing = sorted(required - set(workflow_files))
    if missing:
        raise SystemExit(f"required consolidated workflows missing: {missing}")
    print("Consolidated workflow layout:")
    for name in workflow_files:
        print(f"- {name}")


if __name__ == "__main__":
    main()
