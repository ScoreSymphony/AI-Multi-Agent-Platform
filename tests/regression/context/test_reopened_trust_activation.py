from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.portability.models import PortableResource
from ai_multi_agent_platform.portability.registry import ImportContext
from ai_multi_agent_platform.portability.template_codecs import snapshot_template
from ai_multi_agent_platform.portability.template_import import TemplateImportMutationHandler
from ai_multi_agent_platform.templates import (
    InMemoryTemplateRepository,
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateEnvironment,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateTrust,
    TemplateType,
    activate_untrusted_revision,
)
from ai_multi_agent_platform.templates.application import (
    ContextualTemplateHandlerRegistry,
    TemplateApplicationService,
    TemplateInstantiationContext,
)

OWNER = OwnerRef(type="user", id="issue-78-trust-owner")
ACTIVATOR = OwnerRef(type="user", id="issue-78-trust-activator")


@dataclass
class _RecordingHandler:
    template_type = TemplateType.AGENT
    calls: list[TemplateRevision] = field(default_factory=list)

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        del revision
        return (TemplateResourceChange(resource_type="agent", action="create"),)

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        del provenance, context
        self.calls.append(revision)
        return ()


def _application() -> tuple[TemplateApplicationService, _RecordingHandler]:
    registry = ContextualTemplateHandlerRegistry()
    handler = _RecordingHandler()
    registry.register(handler)
    return TemplateApplicationService(InMemoryTemplateRepository(), registry), handler


def _content(
    name: str,
    *,
    trust: TemplateTrust,
    template_type: TemplateType = TemplateType.AGENT,
    dependencies: tuple[TemplateDependency, ...] = (),
) -> TemplateContent:
    return TemplateContent(
        name=name,
        description=name,
        template_type=template_type,
        configuration=TemplateConfiguration(payload={"name": name}),
        dependencies=dependencies,
        provenance=TemplateProvenance(
            author="source-author",
            source="portable-source",
            trust=trust,
            metadata={"source_marker": "preserved"},
        ),
    )


def _publish(
    application: TemplateApplicationService,
    content: TemplateContent,
) -> TemplateRevision:
    draft = application.templates.create_draft(owner_ref=OWNER, content=content)
    return application.templates.publish(draft.template_id, expected_revision=draft.revision)


def test_untrusted_published_template_is_denied_before_resource_creation() -> None:
    async def scenario() -> None:
        application, handler = _application()
        published = _publish(
            application,
            _content("Imported agent", trust=TemplateTrust.UNTRUSTED),
        )

        with pytest.raises(ContractError) as exc_info:
            await application.apply(
                published.template_id,
                applied_by=OWNER,
                environment=TemplateEnvironment(),
                revision=published.revision,
            )

        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert exc_info.value.details["untrusted_templates"] == [
            f"{published.template_id}@{published.revision}"
        ]
        assert handler.calls == []

    asyncio.run(scenario())


def test_activation_appends_trusted_revision_and_preserves_untrusted_source() -> None:
    async def scenario() -> None:
        application, handler = _application()
        published = _publish(
            application,
            _content("Imported agent", trust=TemplateTrust.UNTRUSTED),
        )

        activated = activate_untrusted_revision(
            application.repository,
            published.template_id,
            expected_revision=published.revision,
            activated_by=ACTIVATOR,
        )

        assert activated.revision == published.revision + 1
        assert activated.content.provenance.trust is TemplateTrust.TRUSTED
        assert activated.content.provenance.source_template == published.ref
        assert activated.content.provenance.author == ACTIVATOR.id
        assert activated.content.provenance.metadata["activated_from_trust"] == "untrusted"
        assert activated.content.provenance.metadata["source_marker"] == "preserved"
        original = application.repository.get_revision(
            published.template_id,
            published.revision,
        )
        assert original.content.provenance.trust is TemplateTrust.UNTRUSTED
        assert original.content.provenance.source_template is None

        instance = await application.apply(
            published.template_id,
            applied_by=ACTIVATOR,
            environment=TemplateEnvironment(),
        )
        assert instance.source == activated.ref
        assert len(handler.calls) == 1
        assert handler.calls[0].ref == activated.ref

        with pytest.raises(ContractError) as old_error:
            await application.apply(
                published.template_id,
                applied_by=ACTIVATOR,
                environment=TemplateEnvironment(),
                revision=published.revision,
            )
        assert old_error.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_untrusted_dependency_blocks_whole_graph_before_any_handler_runs() -> None:
    async def scenario() -> None:
        application, handler = _application()
        dependency = _publish(
            application,
            _content("Untrusted dependency", trust=TemplateTrust.UNTRUSTED),
        )
        root = _publish(
            application,
            _content(
                "Trusted composite",
                trust=TemplateTrust.TRUSTED,
                template_type=TemplateType.COMPOSITE,
                dependencies=(
                    TemplateDependency(
                        template_id=dependency.template_id,
                        revision=dependency.revision,
                    ),
                ),
            ),
        )

        with pytest.raises(ContractError) as exc_info:
            await application.apply(
                root.template_id,
                applied_by=OWNER,
                environment=TemplateEnvironment(),
                revision=root.revision,
            )
        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert (
            f"{dependency.template_id}@{dependency.revision}"
            in exc_info.value.details["untrusted_templates"]
        )
        assert handler.calls == []

    asyncio.run(scenario())


def test_activation_requires_current_published_untrusted_revision() -> None:
    application, _ = _application()
    draft = application.templates.create_draft(
        owner_ref=OWNER,
        content=_content("Draft", trust=TemplateTrust.UNTRUSTED),
    )

    with pytest.raises(ContractError) as draft_error:
        activate_untrusted_revision(
            application.repository,
            draft.template_id,
            expected_revision=draft.revision,
            activated_by=ACTIVATOR,
        )
    assert draft_error.value.code is ErrorCode.CONFLICT

    trusted = _publish(
        application,
        _content("Already trusted", trust=TemplateTrust.TRUSTED),
    )
    with pytest.raises(ContractError) as trusted_error:
        activate_untrusted_revision(
            application.repository,
            trusted.template_id,
            expected_revision=trusted.revision,
            activated_by=ACTIVATOR,
        )
    assert trusted_error.value.code is ErrorCode.CONFLICT


def test_portable_template_import_downgrades_source_trust_without_losing_provenance() -> None:
    async def scenario() -> None:
        source = InMemoryTemplateRepository()
        source_application = TemplateApplicationService(source)
        draft = source_application.templates.create_draft(
            owner_ref=OWNER,
            content=_content("Portable local", trust=TemplateTrust.LOCAL),
        )
        published = source_application.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )
        snapshot = snapshot_template(source, published.template_id)

        target = InMemoryTemplateRepository()
        handler = TemplateImportMutationHandler(target)
        resource = PortableResource(
            resource_type="template",
            resource_id=published.template_id,
            resource_version=str(published.revision),
            payload={},
        )
        await handler.preflight(resource, snapshot, ImportContext())
        await handler.apply(resource, snapshot, ImportContext())

        imported = target.get_revision(published.template_id, published.revision)
        assert imported.content.provenance.trust is TemplateTrust.UNTRUSTED
        assert imported.content.provenance.author == "source-author"
        assert imported.content.provenance.source == "portable-source"
        assert imported.content.provenance.metadata["source_marker"] == "preserved"
        assert imported.content.provenance.metadata["imported_source_trust"] == "local"
        assert (
            source.get_revision(
                published.template_id,
                published.revision,
            ).content.provenance.trust
            is TemplateTrust.LOCAL
        )

    asyncio.run(scenario())
