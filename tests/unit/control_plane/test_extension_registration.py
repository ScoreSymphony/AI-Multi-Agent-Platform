from control_plane_extension_cases import (
    test_extension_registration_rejects_existing_and_builtin_routes,
    test_issue_32_foundation_is_separate_from_later_implemented_domains,
)

__all__ = [name for name in globals() if name.startswith("test_")]
