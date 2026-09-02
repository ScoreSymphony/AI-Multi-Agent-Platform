from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    if old not in text:
        raise SystemExit(f"{label} target not found in {path}")
    path.write_text(text.replace(old, new, 1))


control = Path("tests/test_control_plane.py")
replace_once(
    control,
    '''        run_events = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/tasks/{task_id}/timeline",
                query={"filter[subject_id]": run_id},
            )
        )
        assert_page(run_events.body, total=1)
''',
    '''        run_events = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/tasks/{task_id}/timeline",
                query={"filter[subject_id]": run_id},
            )
        )
        run_items = assert_page(run_events.body)
        assert run_items
        assert all(
            isinstance(item, dict) and item.get("subject_id") == run_id for item in run_items
        )
''',
    "timeline assertion",
)

replace_once(
    control,
    '''def test_openapi_is_current_scope_only_and_documents_evolution() -> None:
    spec = build_openapi()
    assert spec["openapi"] == "3.1.0"
    paths = spec["paths"]
    for resource in (
        "projects",
        "workspaces",
        "tasks",
        "runs",
        "plans",
        "steps",
        "artifacts",
        "results",
    ):
        assert f"/api/v1/{resource}" in paths
    assert "/api/v1/tasks/{task_id}/timeline" in paths
    assert "/api/v1/tasks/{task_id}/events/stream" in paths
    for future_resource in (
        "agents",
        "models",
        "tools",
        "nodes",
        "automations",
        "evaluations",
        "plugins",
    ):
        assert f"/api/v1/{future_resource}" not in paths
    assert spec["x-evolution-policy"]["breaking_changes"] == "require a new major path namespace"
''',
    '''def test_openapi_documents_full_issue_32_scope_and_evolution() -> None:
    spec = build_openapi()
    assert spec["openapi"] == "3.1.0"
    paths = spec["paths"]
    for resource in (
        "projects",
        "workspaces",
        "tasks",
        "plans",
        "steps",
        "runs",
        "agents",
        "teams",
        "artifacts",
        "results",
        "files",
        "memory",
        "knowledge",
        "models",
        "providers",
        "tools",
        "capabilities",
        "nodes",
        "workers",
        "approvals",
        "automations",
        "evaluations",
        "plugins",
        "adapters",
    ):
        assert f"/api/v1/{resource}" in paths
    assert "/api/v1/tasks/{task_id}/timeline" in paths
    assert "/api/v1/tasks/{task_id}/events/stream" in paths
    assert spec["x-evolution-policy"]["breaking_changes"] == "require a new major path namespace"
''',
    "OpenAPI full-scope assertion",
)

issue10 = Path("tests/test_issue_10_openai_provider_runtime.py")
replace_once(
    issue10,
    '''        return HttpJsonResponse(
            200,
            {
                "model": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "local answer"},
                        "finish_reason": "stop",
                    }
                ],
''',
    '''        structured = payload is not None and payload.get("response_format") is not None
        content = '{"answer":"local answer"}' if structured else "local answer"
        return HttpJsonResponse(
            200,
            {
                "model": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
''',
    "issue #10 structured-output fixture",
)
replace_once(
    issue10,
    '''    assert response.text == "local answer"
''',
    '''    assert response.text == '{"answer":"local answer"}'
    assert response.structured_output == {"answer": "local answer"}
''',
    "issue #10 response assertion",
)
