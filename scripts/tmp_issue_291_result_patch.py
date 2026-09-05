from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}: {old[:100]!r}")
    file.write_text(text.replace(old, new, 1))


auth = "src/ai_multi_agent_platform/security/authorization.py"
replace_once(
    auth,
    '    VERIFICATION_READ = "verification:read"\n    VERIFICATION_REVIEW_LIST = "verification-review:list"\n',
    '    VERIFICATION_READ = "verification:read"\n'
    '    VERIFICATION_RESULT_LIST = "verification-result:list"\n'
    '    VERIFICATION_RESULT_READ = "verification-result:read"\n'
    '    VERIFICATION_REVIEW_LIST = "verification-review:list"\n',
)

control = "src/ai_multi_agent_platform/verification/control_plane.py"
replace_once(
    control,
    'VERIFICATION_COLLECTION = "verifications"\nVERIFICATION_REVIEW_COLLECTION = "verification-reviews"\n',
    'VERIFICATION_COLLECTION = "verifications"\n'
    'VERIFICATION_RESULT_COLLECTION = "verification-results"\n'
    'VERIFICATION_REVIEW_COLLECTION = "verification-reviews"\n',
)
replace_once(
    control,
    "\n\nclass VerificationReviewQueueResourceService(ResourceService):\n",
    '''

class VerificationResultResourceService(ResourceService):
    """Task-scoped read view of canonical Verification Results."""

    def __init__(
        self,
        control_plane: ControlPlane,
        verification: VerificationService,
    ) -> None:
        self._control_plane = control_plane
        self._verification = verification

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[tuple[VerificationResult, dict[str, JsonValue]]] = []
        for task_id in await _task_ids(self._control_plane):
            task = await self._control_plane._kernel.get_task(task_id)
            if not await _allowed_for_task(
                self._control_plane,
                context,
                "verification-result:list",
                task,
                resource_ref=task_id,
            ):
                continue
            for request, result in self._verification.history(task_id=task_id):
                if result is not None:
                    resources.append((result, _verification_result_resource(request, result)))
        resources.sort(
            key=lambda item: (item[0].completed_at, item[0].verification_result_id)
        )
        return tuple(resource for _result, resource in resources)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        """Enumerate privacy-safe canonical Result projections for Search rebuild."""

        resources: list[tuple[VerificationResult, dict[str, JsonValue]]] = []
        for task_id in await _task_ids(self._control_plane):
            task = await self._control_plane._kernel.get_task(task_id)
            for request, result in self._verification.history(task_id=task_id):
                if result is None:
                    continue
                resources.append(
                    (
                        result,
                        _search_scoped_resource(
                            _verification_result_search_resource(request, result),
                            task,
                        ),
                    )
                )
        resources.sort(
            key=lambda item: (item[0].completed_at, item[0].verification_result_id)
        )
        return tuple(resource for _result, resource in resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        task, request, result = await _verification_result_entry(
            self._control_plane,
            self._verification,
            resource_id,
        )
        await _authorize_for_task(
            self._control_plane,
            context,
            "verification-result:read",
            task,
            resource_ref=result.verification_result_id,
        )
        return _verification_result_resource(request, result)


class VerificationReviewQueueResourceService(ResourceService):
''',
)
replace_once(
    control,
    '    control_plane.register_resource_service(\n        VERIFICATION_REVIEW_COLLECTION,\n        VerificationReviewQueueResourceService(control_plane, verification),\n    )\n',
    '    control_plane.register_resource_service(\n'
    '        VERIFICATION_RESULT_COLLECTION,\n'
    '        VerificationResultResourceService(control_plane, verification),\n'
    '    )\n'
    '    control_plane.register_resource_service(\n'
    '        VERIFICATION_REVIEW_COLLECTION,\n'
    '        VerificationReviewQueueResourceService(control_plane, verification),\n'
    '    )\n',
)
replace_once(
    control,
    "\n\nasync def _authorize_for_task(\n",
    '''

async def _verification_result_entry(
    control_plane: ControlPlane,
    verification: VerificationService,
    resource_id: str,
) -> tuple[TaskState, VerificationRequest, VerificationResult]:
    for task_id in await _task_ids(control_plane):
        task = await control_plane._kernel.get_task(task_id)
        for request, result in verification.history(task_id=task_id):
            if result is not None and result.verification_result_id == resource_id:
                return task, request, result
    raise ContractError(ErrorCode.NOT_FOUND, "verification result was not found")


async def _authorize_for_task(
''',
)
replace_once(
    control,
    "\n\ndef _requirement_resource(\n",
    '''

def _verification_result_resource(
    request: VerificationRequest,
    result: VerificationResult,
) -> dict[str, JsonValue]:
    resource = _result_resource(result)
    resource.update(
        {
            "type": "verification_result",
            "task_id": request.task_id,
            "run_id": request.run_id,
            "result_id": request.result_id,
            "artifact_ids": list(request.artifact_ids),
            "capability_ids": list(request.capability_ids),
            "policy_id": request.policy_id,
            "policy_version": request.policy_version,
            "stage_id": request.stage_id,
        }
    )
    return resource


def _verification_result_search_resource(
    request: VerificationRequest,
    result: VerificationResult,
) -> dict[str, JsonValue]:
    """Project Result discovery metadata without findings, evidence, digest or human refs."""

    return {
        "id": result.verification_result_id,
        "type": "verification_result",
        "verification_id": result.verification_id,
        "task_id": request.task_id,
        "run_id": request.run_id,
        "result_id": request.result_id,
        "artifact_ids": list(request.artifact_ids),
        "capability_ids": list(request.capability_ids),
        "policy_id": request.policy_id,
        "policy_version": request.policy_version,
        "stage_id": request.stage_id,
        "status": result.outcome.value,
        "outcome": result.outcome.value,
        "subject": {
            "type": result.subject.subject_type,
            "id": result.subject.subject_id,
            "revision": result.subject.revision,
        },
        "verifier": {
            "kind": result.verifier.kind.value,
            "agent_id": result.verifier.agent_id,
            "agent_revision": result.verifier.agent_revision,
            "model_config_id": result.verifier.model_config_id,
            "provider_id": result.verifier.provider_id,
            "read_only": result.verifier.read_only,
        },
        "started_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat(),
    }


def _requirement_resource(
''',
)

indexing = "src/ai_multi_agent_platform/search/indexing.py"
replace_once(
    indexing,
    '    if resource_type == "verification_requirement":\n        task_id = _optional_string(resource, "task_id")\n',
    '    if resource_type == "verification_result":\n'
    '        verification_id = _optional_string(resource, "verification_id")\n'
    '        if verification_id is not None:\n'
    '            return f"Verification result for {verification_id}"\n'
    '    if resource_type == "verification_requirement":\n'
    '        task_id = _optional_string(resource, "task_id")\n',
)
replace_once(
    indexing,
    '    if resource_type == "verification_policy":\n        return _optional_string(resource, "created_at")\n',
    '    if resource_type == "verification_policy":\n'
    '        return _optional_string(resource, "created_at")\n'
    '    if resource_type == "verification_result":\n'
    '        return _optional_string(resource, "completed_at") or _optional_string(\n'
    '            resource, "started_at"\n'
    '        )\n',
)
replace_once(
    indexing,
    '    if resource_type == "verification":\n        add_scalar(_nested_mapping(resource, "policy"), "id", "version")\n',
    '    if resource_type == "verification_result":\n'
    '        add_scalar(resource, "verification_id", "policy_id", "policy_version", "outcome")\n'
    '        add_subject(resource)\n'
    '        verifier = _nested_mapping(resource, "verifier")\n'
    '        add_scalar(verifier, "kind", "agent_id", "agent_revision", "model_config_id", "provider_id")\n'
    '    elif resource_type == "verification":\n'
    '        add_scalar(_nested_mapping(resource, "policy"), "id", "version")\n',
)

search_doc = "docs/SEARCH.md"
search_text = Path(search_doc).read_text()
search_text = search_text.replace(
    "- Evaluation Runs;\n- Connector Definitions;",
    "- Evaluation Runs;\n- Verification Policies, Requests and Results;\n- Connector Definitions;",
    1,
)
search_text = search_text.replace(
    "- Verification Requests/Results/Policies after the currently reopened #86 is stable;\n",
    "",
    1,
)
Path(search_doc).write_text(search_text)

focused_doc = "docs/SEARCH_VERIFICATION.md"
focused_text = Path(focused_doc).read_text()
focused_text = focused_text.replace(
    "- Verification Requests together with the presence and safe outcome metadata of their canonical Verification Result (`verification`);\n- task-scoped Verification Requirements (`verification_requirement`).",
    "- Verification Requests (`verification`);\n- canonical Verification Results through a derived read/search view (`verification_result`);\n- task-scoped Verification Requirements (`verification_requirement`).",
    1,
)
focused_text = focused_text.replace(
    "Search indexes only explicitly safe nested metadata. It may include canonical Task/Run/Result/Artifact relationships, policy/version, stage, outcome, requested verifier kind, machine-verifier Agent/model/provider identifiers, exact subject type/id/revision, and policy scope/stage metadata.",
    "Search indexes only explicitly safe nested metadata. It may include canonical Task/Run/Result/Artifact relationships, policy/version, stage, outcome, requested/verifying kind, machine-verifier Agent/model/provider identifiers, exact subject type/id/revision, and policy scope/stage metadata. Verification Results receive their own `verification-results` read/search view, but that view is derived from #86 request/result history and owns no lifecycle state.",
    1,
)
Path(focused_doc).write_text(focused_text)


test = "tests/test_issue_291_verification_search.py"
test_text = Path(test).read_text()
test_text = test_text.replace(
    "    VERIFICATION_REQUIREMENT_COLLECTION,\n    VERIFICATION_REVIEW_COLLECTION,",
    "    VERIFICATION_REQUIREMENT_COLLECTION,\n    VERIFICATION_RESULT_COLLECTION,\n    VERIFICATION_REVIEW_COLLECTION,",
    1,
)
test_text = test_text.replace(
    '            "verification:list",\n            "verification-requirement:list",',
    '            "verification:list",\n            "verification-result:list",\n            "verification-requirement:list",',
    1,
)
result_assertions = '''
        exact_result = await _search(
            http,
            type="verification_result",
            id=result.verification_result_id,
        )
        assert exact_result["total"] == 1
        result_item = _items(exact_result)[0]
        assert result_item["resource_id"] == result.verification_result_id
        assert result_item["title"] == f"Verification result for {request.verification_id}"
        assert result_item["project_id"] == project_id
        assert result_item["owner_type"] == "user"
        assert result_item["owner_id"] == "alice"
        assert result_item["status"] == "pass"
        assert result_item["updated_at"] == result.completed_at.isoformat()
        assert result_item["canonical_ref"] == (
            f"/api/v1/{VERIFICATION_RESULT_COLLECTION}/{result.verification_result_id}"
        )
        for query_value in (
            request.verification_id,
            task.task_id,
            request.result_id,
            policy.policy_id,
            str(policy.version),
            artifact_id,
            capability_id,
            "human-review",
            "human",
            "pass",
        ):
            page = await _search(http, type="verification_result", q=str(query_value))
            assert page["total"] == 1, (query_value, page)

'''
test_text = test_text.replace(
    '        policy_ref = f"{policy.policy_id}@{policy.version}"\n',
    result_assertions + '        policy_ref = f"{policy.policy_id}@{policy.version}"\n',
    1,
)
test_text = test_text.replace(
    '{"verification": exact, "policy": policy_page, "requirement": requirement},',
    '{\n                "verification": exact,\n                "verification_result": exact_result,\n                "policy": policy_page,\n                "requirement": requirement,\n            },',
    1,
)
test_text = test_text.replace(
    '                "verification:list",\n                "verification-requirement:list",',
    '                "verification:list",\n                "verification-result:list",\n                "verification-requirement:list",',
    1,
)
test_text = test_text.replace(
    '        hidden_requirement = await _search(\n',
    '        hidden_result = await _search(\n'
    '            http,\n'
    '            type="verification_result",\n'
    '            q=hidden_request.verification_id,\n'
    '        )\n'
    '        hidden_requirement = await _search(\n',
    1,
)
test_text = test_text.replace(
    '        assert hidden_verification["total"] == 0\n        assert hidden_requirement["total"] == 0\n',
    '        assert hidden_verification["total"] == 0\n'
    '        assert hidden_result["total"] == 0\n'
    '        assert hidden_requirement["total"] == 0\n',
    1,
)
test_text = test_text.replace(
    '                "verification": hidden_verification,\n                "requirement": hidden_requirement,',
    '                "verification": hidden_verification,\n'
    '                "verification_result": hidden_result,\n'
    '                "requirement": hidden_requirement,',
    1,
)
test_text = test_text.replace(
    '            if call.action in {"verification:list", "verification-requirement:list"}\n',
    '            if call.action\n'
    '            in {\n'
    '                "verification:list",\n'
    '                "verification-result:list",\n'
    '                "verification-requirement:list",\n'
    '            }\n',
    1,
)
test_text = test_text.replace(
    '        assert any(call.action == "verification:list" for call in denied_calls)\n',
    '        assert any(call.action == "verification:list" for call in denied_calls)\n'
    '        assert any(call.action == "verification-result:list" for call in denied_calls)\n',
    1,
)
Path(test).write_text(test_text)
