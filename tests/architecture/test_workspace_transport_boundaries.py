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


def test_workspace_transport_facade_delegates_worker_materialization_store() -> None:
    definitions = _top_level_definitions("workspace_transport.py")
    assert "WorkerWorkspaceMaterializationStore" not in definitions
    assert "WorkerWorkspaceMaterializationStore" in _relative_imports(
        "workspace_transport.py",
        "workspace_materialization_store",
    )
    assert "WorkerWorkspaceMaterializationStore" in _top_level_definitions(
        "workspace_materialization_store.py"
    )


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
    assert not _imports_workspace_transport("workspace_materialization_store.py")
    assert not _imports_workspace_transport("workspace_transport_codec.py")
