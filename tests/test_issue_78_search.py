from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    AuthorizationRequest,
    JsonValue,
)
from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.search import SearchMode, SearchQuery
from ai_multi_agent_platform.templates import (
    ContextualTemplateHandlerRegistry,
    InMemoryTemplateRepository,
    TemplateApplicationService,
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateProvenance,
    TemplateType,
    register_template_control_plane,
)
from ai_multi_agent_platform.templates.control_plane import TemplateResourceService
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class _ScopedAuthorization(FakeAuthorizationProvider):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        denied_scope = request.action == "template:list" and request.context.owner_id in {
            "blocked-owner",
            "org-blocked",
        }
        return AuthorizationDecision(allowed=not denied_scope, reason="issue-78-scope-test")


def _stack() -> tuple[ControlPlane, TemplateApplicationService, _ScopedAuthorization]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    authorization = _ScopedAuthorization()
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        authorization=authorization,
    )
    application = TemplateApplicationService(
        InMemoryTemplateRepository(),
        ContextualTemplateHandlerRegistry(),
    )
    register_template_control_plane(control_plane, application)
    return control_plane, application, authorization


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-template-search",
        correlation_id="correlation-template-search",
        actor=ActorContext(
            principal_ref="user:searcher",
            owner_type="user",
            owner_id="searcher",
        ),
    )


def _content(
    name: str,
    *,
    template_type: TemplateType = TemplateType.AGENT,
    description: str = "Searchable Template metadata",
    tags: tuple[str, ...] = (),
    dependencies: tuple[TemplateDependency, ...] = (),
    marker: str | None = None,
    source: str = "issue-78-search-source",
    author: str = "issue-78-search-author",
) -> TemplateContent:
    payload: dict[str, JsonValue] = {"profile": {"name": name}}
    if marker is not None:
        payload["credential_ref"] = marker
    return TemplateContent(
        name=name,
        description=description,
        template_type=template_type,
        configuration=TemplateConfiguration(payload=payload),
        dependencies=dependencies,
        provenance=TemplateProvenance(author=author, source=source),
        tags=tags,
    )


def _items(page: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    raw_items = page["items"]
    assert isinstance(raw_items, list)
    items: list[dict[str, JsonValue]] = []
    for raw_item in raw_items:
        assert isinstance(raw_item, dict)
        items.append(raw_item)
    return items


def test_global_search_discovers_safe_template_metadata_and_never_configuration_values() -> None:
    async def scenario() -> None:
        control_plane, application, _ = _stack()
        repository = application.repository
        dependency = application.templates.create_draft(
            owner_ref=OwnerRef(type="user", id="allowed-owner"),
            content=_content("Dependency Template"),
        )
        root = application.templates.create_draft(
            owner_ref=OwnerRef(type="user", id="allowed-owner"),
            content=_content(
                "Portable Review Stack",
                template_type=TemplateType.COMPOSITE,
                description="Reusable multi-agent review topology",
                tags=("starter", "review"),
                dependencies=(TemplateDependency(dependency.template_id, dependency.revision),),
                marker="credential://DO-NOT-INDEX-THIS-REFERENCE",
                source="canonical-template-export",
                author="template-author",
            ),
        )
        published = application.templates.publish(
            root.template_id,
            expected_revision=root.revision,
        )

        exact = await control_plane.search_resources(
            _context(),
            SearchQuery(
                exact_id=published.template_id,
                resource_types=("template",),
                mode=SearchMode.EXACT,
            ),
        )
        assert exact["total"] == 1
        item = _items(exact)[0]
        assert item["resource_id"] == published.template_id
        assert item["title"] == "Portable Review Stack"
        assert item["summary"] == "Reusable multi-agent review topology"
        assert item["version"] == str(published.revision)
        assert item["canonical_ref"] == f"/api/v1/templates/{published.template_id}"
        provenance = item["provenance"]
        assert isinstance(provenance, dict)
        assert provenance["collection"] == "templates"
        assert item["access"] == "authorized"

        by_type = await control_plane.search_resources(
            _context(),
            SearchQuery(text="composite", resource_types=("template",)),
        )
        assert published.template_id in {result["resource_id"] for result in _items(by_type)}

        by_tag = await control_plane.search_resources(
            _context(),
            SearchQuery(
                resource_types=("template",),
                tags=("starter",),
                mode=SearchMode.METADATA,
            ),
        )
        assert [result["resource_id"] for result in _items(by_tag)] == [published.template_id]

        by_dependency = await control_plane.search_resources(
            _context(),
            SearchQuery(text=dependency.template_id, resource_types=("template",)),
        )
        assert published.template_id in {result["resource_id"] for result in _items(by_dependency)}

        for provenance_term in ("canonical-template-export", "template-author"):
            by_provenance = await control_plane.search_resources(
                _context(),
                SearchQuery(text=provenance_term, resource_types=("template",)),
            )
            assert published.template_id in {
                result["resource_id"] for result in _items(by_provenance)
            }

        hidden = await control_plane.search_resources(
            _context(),
            SearchQuery(
                text="DO-NOT-INDEX-THIS-REFERENCE",
                resource_types=("template",),
            ),
        )
        assert hidden["total"] == 0
        assert _items(hidden) == []

        search_resources = await TemplateResourceService(repository).list_search_resources()
        root_search_resource = next(
            resource for resource in search_resources if resource["id"] == root.template_id
        )
        assert "revision" not in root_search_resource
        assert "revisions" not in root_search_resource
        assert "configuration" not in root_search_resource
        assert "requirements" not in root_search_resource
        assert "credential_ref" not in repr(root_search_resource)
        assert (
            repository.get_revision(root.template_id, 1).content.configuration.payload is not None
        )

    asyncio.run(scenario())


def test_template_search_authorization_hides_exact_id_counts_and_organization_scope() -> None:
    async def scenario() -> None:
        control_plane, application, authorization = _stack()
        blocked = application.templates.create_draft(
            owner_ref=OwnerRef(type="user", id="blocked-owner"),
            content=_content("Private User Template"),
        )
        organization = application.templates.create_draft(
            owner_ref=OwnerRef(type="organization", id="org-blocked"),
            organization_id="org-blocked",
            content=_content("Private Organization Template"),
        )
        ambiguous = application.templates.create_draft(
            owner_ref=OwnerRef(type="user", id="allowed-owner"),
            organization_id="different-organization",
            content=_content("Ambiguous Organization Template"),
        )

        for template_id in (
            blocked.template_id,
            organization.template_id,
            ambiguous.template_id,
        ):
            result = await control_plane.search_resources(
                _context(),
                SearchQuery(
                    exact_id=template_id,
                    resource_types=("template",),
                    mode=SearchMode.EXACT,
                ),
            )
            assert result["total"] == 0
            assert _items(result) == []

        assert any(
            request.action == "template:list"
            and request.context.owner_type == "user"
            and request.context.owner_id == "blocked-owner"
            for request in authorization.calls
        )
        assert any(
            request.action == "template:list"
            and request.context.owner_type == "organization"
            and request.context.owner_id == "org-blocked"
            for request in authorization.calls
        )

    asyncio.run(scenario())


def test_template_search_rebuild_replaces_stale_revision_metadata() -> None:
    async def scenario() -> None:
        control_plane, application, _ = _stack()
        original = application.templates.create_draft(
            owner_ref=OwnerRef(type="user", id="allowed-owner"),
            content=_content("Old Search Name", tags=("old-tag",)),
        )
        published = application.templates.publish(
            original.template_id,
            expected_revision=original.revision,
        )
        revised = application.templates.revise_draft(
            original.template_id,
            _content("New Search Name", tags=("new-tag",)),
            expected_revision=published.revision,
        )

        new_result = await control_plane.search_resources(
            _context(),
            SearchQuery(text="New Search Name", resource_types=("template",)),
        )
        new_items = _items(new_result)
        assert new_result["total"] == 1
        assert new_items[0]["resource_id"] == original.template_id
        assert new_items[0]["version"] == str(revised.revision)

        old_result = await control_plane.search_resources(
            _context(),
            SearchQuery(text="Old Search Name", resource_types=("template",)),
        )
        assert old_result["total"] == 0

        old_tag = await control_plane.search_resources(
            _context(),
            SearchQuery(
                resource_types=("template",),
                tags=("old-tag",),
                mode=SearchMode.METADATA,
            ),
        )
        assert old_tag["total"] == 0

    asyncio.run(scenario())