from __future__ import annotations

from pathlib import Path

path = Path("tests/test_issue_86_authority_integrity.py")
text = path.read_text(encoding="utf-8")
text = text.replace("from dataclasses import replace\n\n", "", 1)
text = text.replace(
    "    AgentRevisionRef,\n"
    "    AgentRunRecord,\n"
    "    AgentRunStatus,\n"
    "    InMemoryAgentRepository,\n",
    "    AgentInstructions,\n"
    "    AgentProfile,\n"
    "    AgentRevisionRef,\n"
    "    AgentRunRecord,\n"
    "    AgentRunStatus,\n"
    "    AgentService,\n"
    "    InMemoryAgentRepository,\n"
    "    InstructionSource,\n",
    1,
)
text = text.replace(
    "from ai_multi_agent_platform.domain import new_id\n",
    "from ai_multi_agent_platform.domain import OwnerRef, new_id\n",
    1,
)
old = '''        agents = InMemoryAgentRepository()
        agents.create_agent_run(
            AgentRunRecord(
                agent_run_id=new_agent_run_id(),
                run_id=run.run_id,
                task_id=task.task_id,
                agent=AgentRevisionRef(agent_id=agent_id, revision=3),'''
new = '''        agents = InMemoryAgentRepository()
        producer_revision = AgentService(agents).create_agent(
            AgentProfile(
                name="Canonical producer",
                role="producer",
                instructions=AgentInstructions(
                    role=InstructionSource(content="Produce the exact canonical result.")
                ),
            ),
            owner_ref=OwnerRef(type="service", id="issue-86"),
            agent_id=agent_id,
        )
        agents.create_agent_run(
            AgentRunRecord(
                agent_run_id=new_agent_run_id(),
                run_id=run.run_id,
                task_id=task.task_id,
                agent=AgentRevisionRef(
                    agent_id=agent_id,
                    revision=producer_revision.revision,
                ),'''
if old not in text:
    raise SystemExit("canonical producer Agent fixture anchor missing")
text = text.replace(old, new, 1)
text = text.replace(
    "        assert request.producer.agent_revision == 3\n",
    "        assert request.producer.agent_revision == producer_revision.revision\n",
    1,
)
path.write_text(text, encoding="utf-8")
