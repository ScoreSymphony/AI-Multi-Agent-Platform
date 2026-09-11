from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DISTRIBUTED = ROOT / "src" / "ai_multi_agent_platform" / "distributed"


def _tree(filename: str) -> ast.Module:
    return ast.parse((DISTRIBUTED / filename).read_text(encoding="utf-8"))


def _top_level_definitions(filename: str) -> set[str]:
    tree = _tree(filename)
    definition_types = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {node.name for node in tree.body if isinstance(node, definition_types)}


def _relative_imports(filename: str, module: str) -> set[str]:
    imported: set[str] = set()
    for node in _tree(filename).body:
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == module:
            imported.update(alias.asname or alias.name for alias in node.names)
    return imported


def _imports_workspace_transport(filename: str) -> bool:
    for node in ast.walk(_tree(filename)):
        if isinstance(node, ast.ImportFrom):
            if node.module in {
                "workspace_transport",
                "ai_multi_agent_platform.distributed.workspace_transport",
            }:
                return True
        elif isinstance(node, ast.Import):
            if any(
                alias.name == "ai_multi_agent_platform.distributed.workspace_transport"
                for alias in node.names
            ):
                return True
    return False


def test_workspace_transport_facade_delegates_all_implementation_responsibilities() -> None:
    definitions = _top_level_definitions("workspace_transport.py")
    expected = {
        "workspace_materialization_store": {"WorkerWorkspaceMaterializationStore"},
        "workspace_remote_materializer": {
            "TransportRemoteWorkspaceMaterializer",
            "WorkspaceDataContextResolver",
        },
        "workspace_transport_endpoint": {"WorkerWorkspaceTransportEndpoint"},
        "workspace_bound_worker": {
            "WorkspaceBoundLocalWorker",
            "WorkspaceLifecycleFactory",
        },
        "workspace_transport_contract": {"worker_workspace_command_topic"},
    }
    for module, names in expected.items():
        assert names.isdisjoint(definitions)
        assert names <= _relative_imports("workspace_transport.py", module)
        assert names <= _top_level_definitions(f"{module}.py")


def test_workspace_transport_facade_delegates_manifest_and_checksum_codecs() -> None:
    definitions = _top_level_definitions("workspace_transport.py")
    assert "_ManifestEntry" not in definitions
    assert "_canonical_snapshot_checksum" not in definitions

    codec_imports = _relative_imports("workspace_transport.py", "workspace_transport_codec")
    assert "_ManifestEntry" in codec_imports
    assert "_canonical_snapshot_checksum" in codec_imports

    codec_definitions = _top_level_definitions("workspace_transport_codec.py")
    assert "_ManifestEntry" in codec_definitions
    assert "_canonical_snapshot_checksum" in codec_definitions


def test_workspace_transport_components_do_not_depend_back_on_facade() -> None:
    focused_modules = (
        "workspace_materialization_store.py",
        "workspace_transport_codec.py",
        "workspace_transport_contract.py",
        "workspace_remote_materializer.py",
        "workspace_transport_endpoint.py",
        "workspace_bound_worker.py",
    )
    for filename in focused_modules:
        assert not _imports_workspace_transport(filename)
