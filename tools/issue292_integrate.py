from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:140]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/ai_multi_agent_platform/connectors/service.py",
    "        for event in result.events:\n            self._validate_event_binding(event, connection, provider)\n        await self.repository.save_checkpoint(result.checkpoint)\n        return result\n",
    "        for event in result.events:\n            self._validate_event_binding(event, connection, provider)\n        if mode is SyncMode.REBUILD:\n            await self.repository.replace_external_resources(connection.id, result.resources)\n        else:\n            for resource in result.resources:\n                await self.repository.save_external_resource(resource)\n        await self.repository.save_checkpoint(result.checkpoint)\n        return result\n",
)

replace_once(
    "src/ai_multi_agent_platform/connectors/control_plane.py",
    "from .models import Connection, ConnectorDefinition, SyncMode\nfrom .service import ConnectorService\n",
    "from .external_resources import (\n    EXTERNAL_RESOURCE_DETACH_COMMAND,\n    register_external_resource_control_plane,\n)\nfrom .models import Connection, ConnectorDefinition, SyncMode\nfrom .service import ConnectorService\n",
)
replace_once(
    "src/ai_multi_agent_platform/connectors/control_plane.py",
    "    \"connection.health\",\n    \"connector.sync\",\n)",
    "    \"connection.health\",\n    \"connector.sync\",\n    EXTERNAL_RESOURCE_DETACH_COMMAND,\n)",
)
replace_once(
    "src/ai_multi_agent_platform/connectors/control_plane.py",
    "    control_plane.register_resource_service(\n        CONNECTION_COLLECTION,\n        ConnectionResourceService(\n            connectors,\n            actor_resolver,\n            include_organization_scoped_search=bool(\n                getattr(control_plane, \"organization_search_visibility_available\", False)\n            ),\n        ),\n    )\n\n    async def create_connection(\n",
    "    control_plane.register_resource_service(\n        CONNECTION_COLLECTION,\n        ConnectionResourceService(\n            connectors,\n            actor_resolver,\n            include_organization_scoped_search=bool(\n                getattr(control_plane, \"organization_search_visibility_available\", False)\n            ),\n        ),\n    )\n    register_external_resource_control_plane(\n        control_plane, connectors, actor_resolver=actor_resolver\n    )\n\n    async def create_connection(\n",
)

replace_once(
    "src/ai_multi_agent_platform/search/indexing.py",
    "        \"provider_type\",\n        \"organization_id\",\n",
    "        \"provider_type\",\n        \"connection_id\",\n        \"native_namespace\",\n        \"native_id\",\n        \"external_version\",\n        \"external_revision\",\n        \"organization_id\",\n",
)

print("issue 292 integration edits applied")
