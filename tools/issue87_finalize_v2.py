from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:120]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_before(path: str, marker: str, addition: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if addition.strip() in text:
        return
    count = text.count(marker)
    if count != 1:
        raise RuntimeError(f"{path}: expected one marker, found {count}: {marker[:120]!r}")
    file.write_text(text.replace(marker, addition + marker, 1), encoding="utf-8")


# ---- Invitation redemption: identity-bound V1 baseline ---------------------
replace_once(
    "src/ai_multi_agent_platform/organizations/service.py",
    "from ai_multi_agent_platform.domain import OwnerRef\n",
    "from ai_multi_agent_platform.domain import OwnerRef, new_id\n",
)
replace_once(
    "src/ai_multi_agent_platform/organizations/service.py",
    "        token_ref: str,\n        team_id: str | None = None,",
    "        token_ref: str | None = None,\n        team_id: str | None = None,",
)
replace_once(
    "src/ai_multi_agent_platform/organizations/service.py",
    "            token_ref=token_ref,\n",
    "            token_ref=token_ref or f\"identity-bound:{new_id('invitation')}\",\n",
)
replace_once(
    "src/ai_multi_agent_platform/organizations/service.py",
    "        if (\n            invitation.intended_identity_ref is not None\n            and invitation.intended_identity_ref != actor_id\n        ):\n            raise ValueError(\"invitation is bound to another identity\")\n",
    "        if invitation.intended_identity_ref is None:\n            raise ValueError(\n                \"invitation is not bound to an authenticated identity and cannot be redeemed\"\n            )\n        if invitation.intended_identity_ref != actor_id:\n            raise ValueError(\"invitation is bound to another identity\")\n",
)
insert_before(
    "src/ai_multi_agent_platform/organizations/service.py",
    "    async def actor_identity_for_scope(\n",
    '''    async def actor_can_discover_organization(\n        self,\n        *,\n        actor_id: str,\n        organization_id: str,\n    ) -> bool:\n        \"\"\"Return live canonical organization visibility for discovery guards.\"\"\"\n        try:\n            organization = await self._repository.get_organization(organization_id)\n        except LookupError:\n            return False\n        if (\n            actor_id == organization.owner_actor_id\n            or actor_id in organization.administrator_actor_ids\n        ):\n            return True\n        memberships = await self._repository.list_memberships(\n            actor_id=actor_id, organization_id=organization_id\n        )\n        return any(item.status is MembershipStatus.ACTIVE for item in memberships)\n\n''',
)

replace_once(
    "src/ai_multi_agent_platform/control_plane/organization_api.py",
    "                    token_ref=_required_string(payload, \"token_ref\"),\n",
    "",
)
replace_once(
    "frontend/src/api/organizations.ts",
    "  token_ref: string;\n",
    "",
)
replace_once(
    "frontend/src/pages/OrganizationsPage.tsx",
    "        expires_at: expiresAt(form),\n        token_ref: `secret:invitation:${crypto.randomUUID()}`,\n",
    "        expires_at: expiresAt(form),\n",
)
replace_once(
    "frontend/src/pages/OrganizationsPage.tsx",
    '<button className="secondary" disabled={invitation.status !== "pending" || busy !== null} onClick={() => onAccept(invitation)}>Accept as current user</button>',
    '<button className="secondary" disabled={invitation.status !== "pending" || invitation.intended_identity_ref === null || busy !== null} onClick={() => onAccept(invitation)}>Accept as current user</button>',
)
replace_once(
    "frontend/src/api/organizations.test.ts",
    '      expires_at: "2026-09-04T00:00:00+00:00",\n      token_ref: "secret:invitation:test",\n',
    '      expires_at: "2026-09-04T00:00:00+00:00",\n',
)
replace_once(
    "frontend/src/api/organizations.test.ts",
    '    expect(calls[10]?.body.token_ref).toBe("secret:invitation:test");\n',
    '    expect(calls[10]?.body).not.toHaveProperty("token_ref");\n',
)
replace_once(
    "tests/test_issue_87_control_plane.py",
    '            expires_at=expires_at.isoformat(),\n            token_ref="secret-ref:one-time-invite",\n            role_refs=["role:member"],\n',
    '            expires_at=expires_at.isoformat(),\n            role_refs=["role:member"],\n',
)
replace_once(
    "tests/test_issue_87_control_plane.py",
    '        assert "token_ref" not in invitation_response.body\n        assert "secret-ref:one-time-invite" not in repr(invitation_response.body)\n',
    '        assert "token_ref" not in invitation_response.body\n',
)
replace_once(
    "tests/test_issue_87_control_plane.py",
    '        assert invitee_read.status == 200\n        assert "secret-ref:one-time-invite" not in repr(invitee_read.body)\n',
    '        assert invitee_read.status == 200\n        assert "token_ref" not in repr(invitee_read.body)\n',
)

# ---- Organization Search projections ---------------------------------------
replace_once(
    "src/ai_multi_agent_platform/control_plane/organization_api.py",
    "    def __init__(self, service: OrganizationService, collection: CollectionName) -> None:\n        self._service = service\n        self._collection = collection\n",
    "    def __init__(self, service: OrganizationService, collection: CollectionName) -> None:\n        self._service = service\n        self._collection = collection\n        self.search_indexable = collection in {\n            ORGANIZATION_COLLECTION, TEAM_COLLECTION, MEMBERSHIP_COLLECTION\n        }\n",
)
insert_before(
    "src/ai_multi_agent_platform/control_plane/organization_api.py",
    "    async def get_resource(\n        self,\n        context: RequestContext,\n        resource_id: str,\n    ) -> dict[str, JsonValue]:\n",
    '''    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:\n        \"\"\"Enumerate actor-independent, privacy-minimal Search projections.\"\"\"\n        if self._collection == ORGANIZATION_COLLECTION:\n            return tuple(\n                _organization_search_resource(item)\n                for item in await self._service.repository.list_organizations()\n            )\n        if self._collection == TEAM_COLLECTION:\n            return tuple(\n                _team_search_resource(item)\n                for item in await self._service.repository.list_teams()\n            )\n        if self._collection == MEMBERSHIP_COLLECTION:\n            return tuple(\n                _membership_search_resource(item)\n                for item in await self._service.repository.list_memberships()\n            )\n        return ()\n\n''',
)
replace_once(
    "src/ai_multi_agent_platform/control_plane/organization_api.py",
    "async def _visible_organization_ids(\n    service: OrganizationService, principal_ref: str\n) -> frozenset[str]:\n    visible: set[str] = set()\n    for organization in await service.repository.list_organizations():\n        if (\n            principal_ref == organization.owner_actor_id\n            or principal_ref in organization.administrator_actor_ids\n        ):\n            visible.add(organization.id)\n    for membership in await service.repository.list_memberships(actor_id=principal_ref):\n        if membership.status is MembershipStatus.ACTIVE:\n            visible.add(membership.organization_id)\n    return frozenset(visible)\n",
    "async def _visible_organization_ids(\n    service: OrganizationService, principal_ref: str\n) -> frozenset[str]:\n    visible: set[str] = set()\n    for organization in await service.repository.list_organizations():\n        if await service.actor_can_discover_organization(\n            actor_id=principal_ref, organization_id=organization.id\n        ):\n            visible.add(organization.id)\n    return frozenset(visible)\n",
)
insert_before(
    "src/ai_multi_agent_platform/control_plane/organization_api.py",
    "def _invitation_resource(value: Invitation) -> dict[str, JsonValue]:\n",
    '''def _organization_search_resource(value: Organization) -> dict[str, JsonValue]:\n    return {\n        \"id\": value.id, \"type\": \"organization\", \"name\": value.name,\n        \"display_name\": value.display_name, \"status\": value.status.value,\n        \"organization_id\": value.id, \"owner_type\": \"organization\",\n        \"owner_id\": value.id, \"updated_at\": value.updated_at.isoformat(),\n    }\n\n\ndef _team_search_resource(value: Team) -> dict[str, JsonValue]:\n    return {\n        \"id\": value.id, \"type\": \"team\", \"organization_id\": value.organization_id,\n        \"name\": value.name, \"description\": value.description,\n        \"status\": value.status.value, \"parent_team_id\": value.parent_team_id,\n        \"owner_type\": \"organization\", \"owner_id\": value.organization_id,\n        \"updated_at\": value.updated_at.isoformat(),\n    }\n\n\ndef _membership_search_resource(value: Membership) -> dict[str, JsonValue]:\n    updated_at = value.revoked_at or value.suspended_at or value.accepted_at\n    return {\n        \"id\": value.id, \"type\": \"membership\",\n        \"organization_id\": value.organization_id, \"team_id\": value.team_id,\n        \"actor_id\": value.actor_id, \"actor_type\": value.actor_type.value,\n        \"status\": value.status.value, \"owner_type\": \"organization\",\n        \"owner_id\": value.organization_id, \"updated_at\": updated_at.isoformat(),\n    }\n\n\n''',
)
replace_once(
    "src/ai_multi_agent_platform/control_plane/organization_visibility.py",
    "class AdministrativeOwnershipVisibility(ResourceService):\n",
    "class AdministrativeOwnershipVisibility(ResourceService):\n    search_indexable = False\n",
)
replace_once(
    "src/ai_multi_agent_platform/control_plane/organization_audit_api.py",
    "class _OrganizationAuditResources(ResourceService):\n",
    "class _OrganizationAuditResources(ResourceService):\n    search_indexable = False\n",
)

# Search keeps organization scope only as internal derived metadata; SearchService
# authorizes candidates before totals and exact-ID existence become caller-visible.
replace_once(
    "src/ai_multi_agent_platform/search/indexing.py",
    "    resource_provider_id = _optional_string(resource, \"provider_id\")\n    if resource_provider_id is not None:\n        provenance[\"resource_provider_id\"] = resource_provider_id\n",
    "    resource_provider_id = _optional_string(resource, \"provider_id\")\n    if resource_provider_id is not None:\n        provenance[\"resource_provider_id\"] = resource_provider_id\n    organization_id = _optional_string(resource, \"organization_id\")\n    if organization_id is not None:\n        provenance[\"organization_id\"] = organization_id\n",
)
replace_once(
    "src/ai_multi_agent_platform/search/indexing.py",
    '        "provider_type",\n        "location",\n',
    '        "provider_type",\n        "organization_id",\n        "team_id",\n        "actor_id",\n        "actor_type",\n        "parent_team_id",\n        "location",\n',
)

# ---- Organization-scoped Connection Search --------------------------------
replace_once(
    "src/ai_multi_agent_platform/connectors/control_plane.py",
    "        actor_resolver: ConnectorControlPlaneActorResolver,\n    ) -> None:\n        self._connectors = connectors\n        self._actor_resolver = actor_resolver\n",
    "        actor_resolver: ConnectorControlPlaneActorResolver,\n        *,\n        include_organization_scoped_search: bool = False,\n    ) -> None:\n        self._connectors = connectors\n        self._actor_resolver = actor_resolver\n        self._include_organization_scoped_search = include_organization_scoped_search\n",
)
replace_once(
    "src/ai_multi_agent_platform/connectors/control_plane.py",
    "        Organization-scoped Connections remain excluded until #87 membership visibility\n        is available to the Search authorization contract.\n",
    "        Organization-scoped Connections are enumerated only when the composed Control\n        Plane advertises the #87 live membership visibility guard.\n",
)
replace_once(
    "src/ai_multi_agent_platform/connectors/control_plane.py",
    "            if connection.organization_id is None\n",
    "            if connection.organization_id is None\n            or self._include_organization_scoped_search\n",
)
replace_once(
    "src/ai_multi_agent_platform/connectors/control_plane.py",
    "    control_plane.register_resource_service(\n        CONNECTION_COLLECTION, ConnectionResourceService(connectors, actor_resolver)\n    )\n",
    "    control_plane.register_resource_service(\n        CONNECTION_COLLECTION,\n        ConnectionResourceService(\n            connectors, actor_resolver,\n            include_organization_scoped_search=bool(\n                getattr(control_plane, \"organization_search_visibility_available\", False)\n            ),\n        ),\n    )\n",
)
# Only the intentionally small search projection gets organization_id. The full
# resource projection already carries it.
needle = '        "project_id": connection.project_id,\n        "requested_scopes": list(connection.requested_scopes),\n'
replace_once(
    "src/ai_multi_agent_platform/connectors/control_plane.py",
    needle,
    '        "project_id": connection.project_id,\n        "organization_id": connection.organization_id,\n        "requested_scopes": list(connection.requested_scopes),\n',
)

# ---- Final organization runtime Search guard -------------------------------
replace_once(
    "src/ai_multi_agent_platform/control_plane/organization_runtime_composition.py",
    "from ai_multi_agent_platform.organizations import OrganizationService, ResourceOwnership\n",
    "from ai_multi_agent_platform.organizations import OrganizationService, ResourceOwnership\nfrom ai_multi_agent_platform.search import SearchResult\n",
)
insert_before(
    "src/ai_multi_agent_platform/control_plane/organization_runtime_composition.py",
    "    def register_resource_service(self, collection: str, service: ResourceService) -> None:\n",
    '''    @property\n    def organization_search_visibility_available(self) -> bool:\n        return self._organization_service is not None\n\n    async def _search_result_allowed(\n        self, context: RequestContext, result: SearchResult\n    ) -> bool:\n        organization_id = _search_result_organization_id(result)\n        if organization_id is not None and self._organization_service is not None:\n            if not await self._organization_service.actor_can_discover_organization(\n                actor_id=context.actor.principal_ref, organization_id=organization_id\n            ):\n                return False\n        return await super()._search_result_allowed(context, result)\n\n''',
)
insert_before(
    "src/ai_multi_agent_platform/control_plane/organization_runtime_composition.py",
    "async def _cross_organization_share_target(\n",
    '''def _search_result_organization_id(result: SearchResult) -> str | None:\n    value = result.provenance.get(\"organization_id\")\n    return value if isinstance(value, str) and value else None\n\n\n''',
)

# ---- Acceptance regressions -------------------------------------------------
Path("tests/test_issue_87_search_and_invitation_security.py").write_text(
    '''from __future__ import annotations\n\nimport asyncio\nfrom datetime import UTC, datetime, timedelta\n\nimport pytest\n\nfrom ai_multi_agent_platform.connectors import (\n    Connection, ConnectionStatus, ConnectorRegistry, ConnectorService,\n    InMemoryConnectorRepository,\n)\nfrom ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane\nfrom ai_multi_agent_platform.contracts.types import HealthStatus\nfrom ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest\nfrom ai_multi_agent_platform.domain import new_id\nfrom ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel\nfrom ai_multi_agent_platform.organizations import (\n    InMemoryOrganizationRepository, MembershipAuthorizationProvider,\n    OrganizationService,\n)\nfrom ai_multi_agent_platform.security.authorization import ActorType\nfrom ai_multi_agent_platform.testing import (\n    FakeAuthorizationProvider, FakeLifecycleBackend, FakeOrchestrator,\n)\n\n\ndef _headers(principal: str) -> dict[str, str]:\n    return {\n        \"X-Principal-Ref\": principal, \"X-Owner-Type\": \"user\",\n        \"X-Owner-Id\": principal.removeprefix(\"user:\"),\n    }\n\n\nasync def _stack() -> tuple[ControlPlaneHTTP, OrganizationService, InMemoryConnectorRepository]:\n    kernel_repository = InMemoryKernelRepository()\n    kernel = PlatformKernel(\n        orchestrator=FakeOrchestrator(), lifecycle=FakeLifecycleBackend(),\n        repository=kernel_repository,\n    )\n    organization_repository = InMemoryOrganizationRepository()\n    organizations = OrganizationService(organization_repository)\n    authorization = MembershipAuthorizationProvider(\n        FakeAuthorizationProvider(), organization_repository\n    )\n    control_plane = ControlPlane(\n        kernel=kernel, events=kernel_repository, authorization=authorization,\n        organization_service=organizations,\n    )\n    connector_repository = InMemoryConnectorRepository()\n    connectors = ConnectorService(connector_repository, ConnectorRegistry())\n    register_connector_control_plane(control_plane, connectors)\n    return ControlPlaneHTTP(control_plane), organizations, connector_repository\n\n\nasync def _search(http: ControlPlaneHTTP, principal: str, **query: str) -> dict[str, object]:\n    response = await http.handle(HTTPRequest(\n        method=\"GET\", path=\"/api/v1/search\", headers=_headers(principal), query=query\n    ))\n    assert response.status == 200, response.body\n    assert isinstance(response.body, dict)\n    return response.body\n\n\ndef _items(page: dict[str, object]) -> list[dict[str, object]]:\n    items = page[\"items\"]\n    assert isinstance(items, list) and all(isinstance(item, dict) for item in items)\n    return items\n\n\ndef test_organization_search_and_connection_search_revoke_visibility_live() -> None:\n    async def scenario() -> None:\n        http, organizations, connector_repository = await _stack()\n        organization = await organizations.create_organization(\n            name=\"Search Org\", owner_actor_id=\"user:owner\"\n        )\n        team = await organizations.create_team(\n            organization_id=organization.id, name=\"Search Team\"\n        )\n        membership = await organizations.add_member(\n            actor_id=\"user:member\", actor_type=ActorType.HUMAN,\n            organization_id=organization.id, team_id=team.id,\n        )\n        connection = Connection(\n            id=new_id(\"connection\"), connector_type_id=\"reference.local\",\n            connector_version=\"1.0\", owner_type=\"user\", owner_id=\"owner\",\n            display_name=\"Organization-scoped search connection\",\n            organization_id=organization.id, status=ConnectionStatus.READY,\n            health=HealthStatus.HEALTHY,\n        )\n        await connector_repository.save_connection(connection)\n\n        org_page = await _search(http, \"user:member\", type=\"organization\", id=organization.id)\n        assert org_page[\"total\"] == 1\n        team_page = await _search(http, \"user:member\", type=\"team\", q=organization.id)\n        assert {item[\"resource_id\"] for item in _items(team_page)} == {team.id}\n        member_page = await _search(http, \"user:member\", type=\"membership\", q=\"user:member\")\n        assert {item[\"resource_id\"] for item in _items(member_page)} == {membership.id}\n        conn_page = await _search(http, \"user:member\", type=\"connection\", id=connection.id)\n        assert conn_page[\"total\"] == 1\n\n        for resource_type, resource_id in (\n            (\"organization\", organization.id), (\"team\", team.id),\n            (\"membership\", membership.id), (\"connection\", connection.id),\n        ):\n            hidden = await _search(http, \"user:outsider\", type=resource_type, id=resource_id)\n            assert hidden[\"total\"] == 0\n            assert resource_id not in repr(hidden)\n\n        await organizations.suspend_member(membership.id)\n        assert (await _search(\n            http, \"user:member\", type=\"organization\", id=organization.id\n        ))[\"total\"] == 0\n        assert (await _search(\n            http, \"user:member\", type=\"connection\", id=connection.id\n        ))[\"total\"] == 0\n\n        owner_member = await _search(\n            http, \"user:owner\", type=\"membership\", id=membership.id\n        )\n        assert owner_member[\"total\"] == 1\n        assert _items(owner_member)[0][\"status\"] == \"suspended\"\n\n    asyncio.run(scenario())\n\n\ndef test_invitation_redemption_requires_authenticated_identity_binding() -> None:\n    async def scenario() -> None:\n        _, organizations, _ = await _stack()\n        now = datetime(2026, 9, 5, 12, tzinfo=UTC)\n        organization = await organizations.create_organization(\n            name=\"Invite Security\", owner_actor_id=\"user:owner\", now=now\n        )\n        email_only = await organizations.invite_member(\n            organization_id=organization.id, invited_by_actor_id=\"user:owner\",\n            intended_email_ref=\"email-ref:invitee\",\n            expires_at=now + timedelta(hours=1), now=now,\n        )\n        with pytest.raises(ValueError, match=\"not bound to an authenticated identity\"):\n            await organizations.accept_invitation(\n                email_only.id, actor_id=\"user:attacker\", now=now + timedelta(minutes=1)\n            )\n        bound = await organizations.invite_member(\n            organization_id=organization.id, invited_by_actor_id=\"user:owner\",\n            intended_identity_ref=\"user:invitee\", intended_email_ref=\"email-ref:invitee\",\n            expires_at=now + timedelta(hours=1), now=now,\n        )\n        with pytest.raises(ValueError, match=\"bound to another identity\"):\n            await organizations.accept_invitation(\n                bound.id, actor_id=\"user:attacker\", now=now + timedelta(minutes=1)\n            )\n        accepted = await organizations.accept_invitation(\n            bound.id, actor_id=\"user:invitee\", now=now + timedelta(minutes=2)\n        )\n        assert accepted.actor_id == \"user:invitee\"\n        assert accepted.organization_id == organization.id\n\n    asyncio.run(scenario())\n''', encoding="utf-8")

# Documentation.
replace_once(
    "docs/ORGANIZATIONS.md",
    "Invitation records contain only a secure `token_ref`; token material and credentials remain outside this domain. Northbound invitation projections deliberately omit `token_ref`, so secret references are not exposed through list/get responses.\n",
    "Invitation persistence keeps any internal `token_ref` outside northbound projections, but the V1 browser/API baseline does not invent or submit secret references. Without a separately implemented one-time credential verifier, redemption is allowed only when `intended_identity_ref` matches the authenticated principal. Email-only invitations may be created, expired or revoked, but cannot be redeemed merely by knowing an Invitation ID.\n",
)
replace_once(
    "docs/ORGANIZATIONS.md",
    "`invitation.accept` is intentionally usable before Membership exists: the invited actor gains the Membership only when acceptance succeeds. Authentication and the generic #15 command boundary still apply.\n",
    "`invitation.accept` is usable before Membership exists only for an authenticated principal matching `intended_identity_ref`; the invited actor gains Membership when acceptance succeeds. Email-only records require a future canonical one-time credential flow before they can be redeemable. Authentication and the generic #15 command boundary still apply.\n\nGlobal Search indexes privacy-minimal Organization, Team and Membership projections only. Live Organization visibility is rechecked before results, totals or exact-ID existence are returned, then the canonical #15 authorization provider still makes the final action decision. The same visibility hook enables organization-scoped Connection discovery without changing canonical Connection ownership. Invitations, ownership/share records, IdP mappings and Organization audit events remain outside global Search.\n",
)
replace_once(
    "docs/ORGANIZATIONS.md",
    "- typed frontend Organization API behavior.\n",
    "- typed frontend Organization API behavior;\n- Organization/Team/Membership Search with cross-Organization non-disclosure and immediate suspension/removal visibility loss;\n- organization-scoped Connection Search guarded by the same live Organization visibility hook;\n- identity-bound invitation redemption without browser-generated secret references.\n",
)

print("issue 87 finalization v2 applied")
