"""Rollback-capable canonical Template import handler."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.templates import (
    TemplateDefinition,
    TemplateRepository,
    TemplateRevisionState,
    validate_template_configuration,
)

from .models import PortableResource
from .registry import ImportContext
from .template_codecs import TEMPLATE_RESOURCE_TYPE, TemplatePortableSnapshot


class TemplateImportMutationHandler:
    """Restore one complete Template history through the canonical repository seam."""

    resource_type = TEMPLATE_RESOURCE_TYPE

    def __init__(self, repository: TemplateRepository) -> None:
        self._repository = repository

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_snapshot(value)
        _require_missing_template(self._repository, snapshot.definition.template_id)
        for revision in snapshot.revisions:
            validate_template_configuration(revision.content.configuration)

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        snapshot = _require_snapshot(value)
        created = False
        latest_published: int | None = None
        try:
            for index, revision in enumerate(snapshot.revisions):
                if revision.state is TemplateRevisionState.PUBLISHED:
                    latest_published = revision.revision
                definition = _definition_at(
                    snapshot.definition,
                    revision.revision,
                    latest_published,
                    final=index == len(snapshot.revisions) - 1,
                )
                if index == 0:
                    self._repository.create_template(definition, revision)
                    created = True
                else:
                    self._repository.append_revision(definition, revision)
            return snapshot.definition.template_id
        except Exception:
            if created:
                try:
                    self._repository.delete_template(snapshot.definition.template_id)
                except Exception as rollback_error:
                    raise ContractError(
                        ErrorCode.BACKEND_ERROR,
                        "portable Template apply failed and internal compensation also failed",
                        details={"template_id": snapshot.definition.template_id},
                    ) from rollback_error
            raise

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        if not isinstance(token, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Template rollback token must be the imported Template ID",
            )
        self._repository.delete_template(token)


def _definition_at(
    final: TemplateDefinition,
    revision: int,
    latest_published: int | None,
    *,
    final: bool,
) -> TemplateDefinition:
    if final:
        return final
    return replace(
        final,
        current_revision=revision,
        latest_published_revision=latest_published,
    )


def _require_snapshot(value: object) -> TemplatePortableSnapshot:
    if not isinstance(value, TemplatePortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable Template mutation handler received the wrong decoded resource type",
        )
    return value


def _require_missing_template(repository: TemplateRepository, template_id: str) -> None:
    try:
        repository.get_template(template_id)
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return
        raise
    raise ContractError(
        ErrorCode.CONFLICT,
        f"Template appeared after import preview: {template_id}",
        details={"template_id": template_id},
    )


__all__ = ["TemplateImportMutationHandler"]
