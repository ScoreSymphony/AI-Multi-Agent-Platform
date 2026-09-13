"""Skill persistence and restart recovery coverage.

Migrated from the historical Issue #588 root-level suite as part of #722.
"""

from __future__ import annotations

from ai_multi_agent_platform.agents import AgentRevisionRef, AgentRunRecord, AgentRunStatus
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.skills import (
    JsonSkillRepository,
    SkillContent,
    SkillExecutionCoordinator,
    SkillProfile,
    SkillResolutionRequest,
    SkillResolver,
    SkillRevisionRef,
    SkillService,
)

OWNER = OwnerRef(type="user", id="issue-588-test")


def _profile(name: str, content: str) -> SkillProfile:
    return SkillProfile(
        name=name,
        purpose_categories=("test",),
        content=SkillContent(content=content),
    )


def _request(
    *,
    agent_id: str,
    run_id: str,
    task_id: str,
    explicit: tuple[SkillRevisionRef, ...] = (),
) -> SkillResolutionRequest:
    return SkillResolutionRequest(
        run_id=run_id,
        task_id=task_id,
        agent_id=agent_id,
        agent_revision=1,
        agent_role="worker",
        explicit_skills=explicit,
    )


def test_restart_persistence_retains_exact_agent_run_bundle_binding(tmp_path) -> None:
    path = tmp_path / "skills.json"
    repository = JsonSkillRepository(path)
    service = SkillService(repository)
    skill = service.create_skill(_profile("Persisted", "method"), owner_ref=OWNER)
    coordinator = SkillExecutionCoordinator(SkillResolver(repository))
    agent_id = new_id("agent")
    run_id = new_id("run")
    task_id = new_id("task")
    bundle, binding = coordinator.prepare(
        _request(
            agent_id=agent_id,
            run_id=run_id,
            task_id=task_id,
            explicit=(skill.ref,),
        )
    )
    record = AgentRunRecord(
        agent_run_id=new_id("agent_run"),
        run_id=run_id,
        task_id=task_id,
        agent=AgentRevisionRef(agent_id=agent_id, revision=1),
        status=AgentRunStatus.RUNNING,
    )
    bound = coordinator.bind_agent_run(binding.binding_id, record)

    restored = JsonSkillRepository(path)
    restored_binding = restored.get_binding(binding.binding_id)
    restored_bundle = restored.get_bundle(bundle.skill_bundle_id)

    assert restored_binding == bound
    assert restored_binding.agent_run_id == record.agent_run_id
    assert restored_bundle.digest == bundle.digest == restored_binding.skill_bundle_hash
    assert restored_bundle.entries[0].ref == skill.ref
