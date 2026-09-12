from control_plane_extension_cases import (
    test_extension_operations_are_authorized_and_private_fields_are_rejected,
    test_registered_command_receives_actor_correlation_and_idempotency_context,
    test_registered_extension_resource_updates_manifest_openapi_and_routes,
    test_unregistered_future_domains_are_not_predeclared,
)

__all__ = [name for name in globals() if name.startswith("test_")]
