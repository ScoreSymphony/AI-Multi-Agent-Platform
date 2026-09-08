from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"expected patch target missing in {path}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    service = ROOT / "src/ai_multi_agent_platform/compensation/service.py"
    replace_once(
        service,
        "        key = idempotency_key or self._default_idempotency_key(action, trigger)\n",
        "        key = idempotency_key or self._default_idempotency_key(action)\n",
    )
    replace_once(
        service,
        '''    @staticmethod
    def _default_idempotency_key(
        action: CompletedSideEffect,
        trigger: CompensationTrigger,
    ) -> str:
        return (
            f"compensation:{action.group_id}:{action.action_id}:"
            f"plan-revision-{action.plan_revision}:{trigger.value}"
        )
''',
        '''    @staticmethod
    def _default_idempotency_key(action: CompletedSideEffect) -> str:
        return (
            f"compensation:{action.group_id}:{action.action_id}:"
            f"plan-revision-{action.plan_revision}"
        )
''',
    )

    repository = ROOT / "src/ai_multi_agent_platform/compensation/repository.py"
    replace_once(
        repository,
        '''        existing_id = self._request_keys.get(request.idempotency_key)
        if existing_id is not None:
            return self._requests[existing_id]
''',
        '''        existing_id = self._request_keys.get(request.idempotency_key)
        if existing_id is not None:
            existing = self._requests[existing_id]
            if _same_idempotency_target(existing, request):
                return existing
            raise ContractError(
                ErrorCode.CONFLICT,
                "compensation idempotency key already belongs to another target",
            )
''',
    )
    replace_once(
        repository,
        '''        except sqlite3.IntegrityError:
            existing = self.find_request_by_key(request.idempotency_key)
            if existing is not None:
                return existing
            existing_by_id = self.get_request(request.compensation_id)
''',
        '''        except sqlite3.IntegrityError:
            existing = self.find_request_by_key(request.idempotency_key)
            if existing is not None:
                if _same_idempotency_target(existing, request):
                    return existing
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "compensation idempotency key already belongs to another target",
                ) from None
            existing_by_id = self.get_request(request.compensation_id)
''',
    )
    text = repository.read_text()
    helper = '''

def _same_idempotency_target(
    existing: CompensationRequest,
    candidate: CompensationRequest,
) -> bool:
    """Return whether two deliveries identify the same immutable compensation target."""

    return (
        existing.group_id == candidate.group_id
        and existing.action_id == candidate.action_id
        and existing.original_task_id == candidate.original_task_id
        and existing.original_plan_id == candidate.original_plan_id
        and existing.original_plan_revision == candidate.original_plan_revision
        and existing.original_project_id == candidate.original_project_id
        and existing.original_step_id == candidate.original_step_id
        and existing.original_run_id == candidate.original_run_id
        and existing.original_tool_invocation_id == candidate.original_tool_invocation_id
        and existing.original_result_ref == candidate.original_result_ref
        and existing.external_resource_ref == candidate.external_resource_ref
        and existing.requested_capability_id == candidate.requested_capability_id
        and existing.requested_capability_version == candidate.requested_capability_version
    )
'''
    marker = "\n\ndef _group_to_json(group: CompensationGroup) -> dict[str, JsonValue]:\n"
    if "def _same_idempotency_target(" not in text:
        if marker not in text:
            raise RuntimeError("repository helper insertion point missing")
        repository.write_text(text.replace(marker, helper + marker, 1))

    docs = ROOT / "docs/runtime/COMPENSATION.md"
    replace_once(
        docs,
        '''Every `CompensationRequest` has a stable compensation ID and idempotency key. The reference
coordinator derives an automatic key from group, action, Plan revision and trigger; callers may
provide a stronger external key where needed. The repositories enforce key uniqueness, so duplicate
failure/cancellation delivery resolves to the already-created request.
''',
        '''Every `CompensationRequest` has a stable compensation ID and idempotency key. The reference
coordinator derives an automatic key from group, action and Plan revision. Trigger changes do not
create a second default compensation identity for the same completed side effect, so a downstream
failure followed by cancellation or manual recovery cannot repeat an already-requested undo. Callers
may provide a stronger external key where needed. Reusing one key for a different immutable
compensation target is rejected with `conflict` instead of silently aliasing the requests.
''',
    )

    tests = ROOT / "tests/test_issue_596_compensation.py"
    text = tests.read_text()
    import_marker = (
        "from ai_multi_agent_platform.contracts.domain_mapping "
        "import map_tool_invocation_to_domain\n"
    )
    if "from ai_multi_agent_platform.contracts import ContractError, ErrorCode\n" not in text:
        if import_marker not in text:
            raise RuntimeError("test import insertion point missing")
        text = text.replace(
            import_marker,
            "from ai_multi_agent_platform.contracts import ContractError, ErrorCode\n"
            + import_marker,
            1,
        )

    if "def test_default_idempotency_identity_is_stable_across_compensation_triggers" not in text:
        text += r'''


def test_default_idempotency_identity_is_stable_across_compensation_triggers() -> None:
    async def scenario() -> None:
        provider = UndoProvider()
        coordinator = await _coordinator(provider)
        group = coordinator.register_group(_group())
        action = coordinator.record_completed_side_effect(
            _action(group, resource="cross-trigger", execution_order=1)
        )

        failure = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.DOWNSTREAM_FAILURE,
            reason="downstream failed",
            actor_ref="service:coordination",
            correlation_id="corr-cross-trigger-failure",
        )
        cancellation = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.CANCELLATION,
            reason="task was later cancelled",
            actor_ref="service:coordination",
            correlation_id="corr-cross-trigger-cancellation",
        )
        manual = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="operator reviewed recovery",
            actor_ref="user:user-1",
            correlation_id="corr-cross-trigger-manual",
        )

        assert cancellation.compensation_id == failure.compensation_id
        assert manual.compensation_id == failure.compensation_id
        assert cancellation.idempotency_key == failure.idempotency_key
        assert manual.idempotency_key == failure.idempotency_key

        first = await coordinator.execute(failure.compensation_id, _execution_context(group))
        repeated = await coordinator.execute(manual.compensation_id, _execution_context(group, 2))
        assert first.status is CompensationStatus.SUCCEEDED
        assert repeated == first
        assert len(provider.calls) == 1

    asyncio.run(scenario())


def test_explicit_idempotency_key_collision_across_actions_is_rejected(tmp_path: Path) -> None:
    async def exercise(repository) -> None:
        provider = UndoProvider()
        registry = CapabilityRegistry()
        await registry.register_provider(provider)
        invoker = CapabilityInvoker(
            registry,
            canonical_binding_hook=_canonical_binding,
        )
        coordinator = CompensationCoordinator(repository, invoker)
        group = coordinator.register_group(_group())
        first = coordinator.record_completed_side_effect(
            _action(group, resource="collision-first", execution_order=1)
        )
        second = coordinator.record_completed_side_effect(
            _action(group, resource="collision-second", execution_order=2)
        )
        coordinator.request_compensation(
            first.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="first recovery",
            actor_ref="user:user-1",
            correlation_id="corr-key-first",
            idempotency_key="shared-external-recovery-key",
        )

        try:
            coordinator.request_compensation(
                second.action_id,
                trigger=CompensationTrigger.MANUAL,
                reason="second recovery",
                actor_ref="user:user-1",
                correlation_id="corr-key-second",
                idempotency_key="shared-external-recovery-key",
            )
        except ContractError as exc:
            assert exc.code is ErrorCode.CONFLICT
            assert "idempotency key" in exc.message
        else:
            raise AssertionError("cross-action idempotency collision must be rejected")

    asyncio.run(exercise(InMemoryCompensationRepository()))
    asyncio.run(exercise(SQLiteCompensationRepository(tmp_path / "collision.sqlite3")))
'''
    tests.write_text(text)


if __name__ == "__main__":
    main()
