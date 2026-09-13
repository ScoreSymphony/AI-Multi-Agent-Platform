from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.automation import (
    IdentityContext,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment


def test_single_node_deployment_persists_automation_state_by_default(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "single-node")
        first = build_single_node_deployment(config)
        account = first.bootstrap_admin("automation-admin", "correct horse battery staple")

        automation = await first.control_plane.automation_service.create_automation(
            name="durable single-node automation",
            description="Prove the production-shaped deployment wires Automation persistence",
            identity=IdentityContext(
                principal_ref=account.user_id,
                owner_type="user",
                owner_id=account.user_id,
            ),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=TaskTemplate(
                title="Generated from durable deployment",
                objective="Remain persisted across a single-node process restart",
            ),
        )

        automation_db = config.database_dir / "automation.sqlite3"
        assert automation_db.is_file()

        restarted = build_single_node_deployment(config)
        persisted = await restarted.control_plane.automation_service.get_automation(automation.id)
        audit = await restarted.control_plane.automation_runtime_state.list_audit_events()

        assert persisted.id == automation.id
        assert persisted.identity.principal_ref == account.user_id
        assert any(
            event.get("type") == "automation.configuration"
            and event.get("automation_id") == automation.id
            and event.get("action") == "created"
            for event in audit
        )

    asyncio.run(scenario())
