"""Repository-wide test configuration.

Most historical Control Plane contract tests intentionally exercise transport/domain
semantics without installing an authorization policy. Production composition is now
secure-by-default, so tests opt into that legacy unsecured mode explicitly.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

os.environ.setdefault("AI_MULTI_AGENT_PLATFORM_ALLOW_INSECURE_CONTROL_PLANE", "1")

_TEST_ROOT = Path(__file__).resolve().parent
_SUITE_MARKERS = frozenset(
    {
        "unit",
        "contract",
        "integration",
        "e2e",
        "performance",
        "regression",
        "release",
    }
)
_OPENSSL_TESTS = frozenset(
    {
        (
            Path("e2e/files/test_secure_entrypoint_e2e.py"),
            "test_secure_profile_provisioning_worker_entrypoint_and_task_run",
        ),
        (
            Path("regression/workspaces/test_final_hardening.py"),
            "test_tcp_transport_succeeds_with_verified_mtls_identity",
        ),
    }
)
_SYMLINK_TESTS = frozenset(
    {
        (
            Path("performance/evaluation/test_reference_host_absolute_budget.py"),
            "test_absolute_budget_cli_rejects_symlink_output_alias",
        ),
    }
)
_POSIX_LITERAL_TESTS = frozenset(
    {
        (
            Path("regression/context/test_completion_prep.py"),
            "test_projectatlas_source_binding_and_containment_fail_closed",
        ),
    }
)


@lru_cache(maxsize=1)
def _can_create_symlink() -> bool:
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            target.write_text("target", encoding="utf-8")
            link = root / "link.txt"
            link.symlink_to(target)
            return link.is_symlink()
    except (NotImplementedError, OSError):
        return False


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply suite markers and gate tests on optional host capabilities."""
    openssl_available = shutil.which("openssl") is not None
    symlink_available = _can_create_symlink()

    for item in items:
        try:
            relative_path = Path(str(item.path)).resolve().relative_to(_TEST_ROOT)
        except ValueError:
            continue
        if not relative_path.parts:
            continue
        suite = relative_path.parts[0]
        if suite in _SUITE_MARKERS:
            item.add_marker(getattr(pytest.mark, suite))

        test_key = (relative_path, item.name)
        if test_key in _OPENSSL_TESTS and not openssl_available:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "OpenSSL CLI is unavailable; external mTLS acceptance tooling is optional"
                    )
                )
            )
        if test_key in _SYMLINK_TESTS and not symlink_available:
            item.add_marker(
                pytest.mark.skip(
                    reason="host cannot create symbolic links required by this alias-safety test"
                )
            )
        if os.name == "nt" and test_key in _POSIX_LITERAL_TESTS:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "legacy fixture uses POSIX-only absolute path literals; "
                        "platform-neutral ProjectAtlas binding coverage runs separately"
                    )
                )
            )
