from __future__ import annotations

import pytest

from ai_multi_agent_platform.applications import (
    Application,
    ApplicationDesiredState,
    ApplicationEndpoint,
    ApplicationEndpointExposure,
    ApplicationEndpointResolution,
    ApplicationHealthStatus,
    ApplicationInstance,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationResourceAssociation,
    ApplicationResourceAssociationResolver,
    ApplicationResourceHandlerService,
    ApplicationService,
    ApplicationServiceRuntime,
    ApplicationUi,
    ApplicationUiOpenMode,
    InMemoryApplicationRepository,
    application_resource_handler_module,
)
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.domain import new_id


def _application(
    *,
    name: str,
    media_types: tuple[str, ...] = (),
    resource_types: tuple[str, ...] = (),
) -> Application:
    application_id = new_id("application")
    manifest = ApplicationManifest(
        application_id=application_id,
        name=name,
        version="1.0.0",
        description=f"{name} fixture",
        services=(
            ApplicationService(
                service_id="web",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("python", "-c", "pass"),
                endpoints=(
                    ApplicationEndpoint(
                        name="ui",
                        target_port=8765,
                        exposure=ApplicationEndpointExposure.USER,
                    ),
                ),
            ),
        ),
        ui=ApplicationUi(
            endpoint_ref="web.ui",
            open_mode=ApplicationUiOpenMode.EXTERNAL,
        ),
        resource_associations=(
            ApplicationResourceAssociation(
                media_types=media_types,
                resource_types=resource_types,
            ),
        ),
    )
    return Application(manifest=manifest, runtime_id="local.process")


def _running_instance(application: Application) -> ApplicationInstance:
    return ApplicationInstance(
        application_id=application.application_id,
        application_version=application.version,
        runtime_id=application.runtime_id,
        desired_state=ApplicationDesiredState.RUNNING,
        observed_state=ApplicationObservedState.RUNNING,
        health=ApplicationHealthStatus.HEALTHY,
        endpoints=(
            ApplicationEndpointResolution(
                endpoint_ref="web.ui",
                uri="http://127.0.0.1:8765/",
                exposure=ApplicationEndpointExposure.USER,
            ),
        ),
    )


def _context() -> RequestContext:
    return RequestContext(request_id="request-test", correlation_id="correlation-test")


def test_resolver_matches_media_type_parameters_and_installed_instance() -> None:
    repository = InMemoryApplicationRepository()
    editor = repository.save_application(
        _application(name="Text editor", media_types=("text/plain",))
    )
    repository.save_application(_application(name="PDF editor", media_types=("application/pdf",)))
    instance = repository.save_instance(_running_instance(editor))

    resolutions = ApplicationResourceAssociationResolver(repository).resolve(
        media_type="Text/Plain; charset=utf-8"
    )

    assert len(resolutions) == 1
    assert resolutions[0].application == editor
    assert resolutions[0].instances == (instance,)
    assert resolutions[0].openable_instances == (instance,)


def test_resolver_requires_every_supplied_selector_to_match_same_association() -> None:
    repository = InMemoryApplicationRepository()
    editor = repository.save_application(
        _application(
            name="Artifact editor",
            media_types=("text/plain",),
            resource_types=("artifact",),
        )
    )

    assert (
        ApplicationResourceAssociationResolver(repository)
        .resolve(
            media_type="text/plain",
            resource_type="ARTIFACT",
        )[0]
        .application
        == editor
    )
    assert (
        ApplicationResourceAssociationResolver(repository).resolve(
            media_type="application/pdf",
            resource_type="artifact",
        )
        == ()
    )


def test_resolver_rejects_missing_or_invalid_selectors() -> None:
    resolver = ApplicationResourceAssociationResolver(InMemoryApplicationRepository())

    with pytest.raises(ValueError, match="media_type or resource_type is required"):
        resolver.resolve()
    with pytest.raises(ValueError, match="MIME type"):
        resolver.resolve(media_type="text")
    with pytest.raises(ValueError, match="resource_type must not be blank"):
        resolver.resolve(resource_type=" ")


@pytest.mark.asyncio
async def test_control_plane_resource_handlers_expose_only_definition_metadata() -> None:
    repository = InMemoryApplicationRepository()
    editor = repository.save_application(
        _application(
            name="Text editor",
            media_types=("text/plain", "text/markdown"),
            resource_types=("file",),
        )
    )
    repository.save_instance(_running_instance(editor))
    service = ApplicationResourceHandlerService(repository)

    resources = await service.list_resources(_context(), PageQuery())

    text_handler = next(item for item in resources if item["media_type"] == "text/plain")
    assert text_handler["application_ref"] == f"{editor.application_id}@1.0.0"
    assert text_handler["association_kind"] == "media_type"
    assert text_handler["association_value"] == "text/plain"
    assert "instance_ids" not in text_handler
    assert "open_instances" not in text_handler
    assert await service.get_resource(_context(), str(text_handler["id"])) == text_handler

    module = application_resource_handler_module(repository)
    assert module.name == "applications.resource-associations"
    assert module.requires == frozenset({"applications"})
    assert "application-resource-handlers" in module.resource_services
