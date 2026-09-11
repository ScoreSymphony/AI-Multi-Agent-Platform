"""Compatibility façade for distributed remote Workspace transport.

Responsibility-focused implementations live in sibling modules while existing imports from
``distributed.workspace_transport`` remain stable.
"""

from .workspace_bound_worker import WorkspaceBoundLocalWorker, WorkspaceLifecycleFactory
from .workspace_materialization_store import WorkerWorkspaceMaterializationStore
from .workspace_remote_materializer import (
    TransportRemoteWorkspaceMaterializer,
    WorkspaceDataContextResolver,
)
from .workspace_transport_codec import (
    _ManifestEntry as _ManifestEntry,
    _canonical_snapshot_checksum as _canonical_snapshot_checksum,
)
from .workspace_transport_contract import (
    DEFAULT_WORKSPACE_CHUNK_BYTES,
    WORKSPACE_COMMAND_TOPIC_PREFIX,
    WORKSPACE_REPLY_TOPIC_PREFIX,
    WORKSPACE_TRANSPORT_SCHEMA_VERSION,
    worker_workspace_command_topic,
)
from .workspace_transport_endpoint import WorkerWorkspaceTransportEndpoint

__all__ = [
    "DEFAULT_WORKSPACE_CHUNK_BYTES",
    "TransportRemoteWorkspaceMaterializer",
    "WORKSPACE_COMMAND_TOPIC_PREFIX",
    "WORKSPACE_REPLY_TOPIC_PREFIX",
    "WORKSPACE_TRANSPORT_SCHEMA_VERSION",
    "WorkerWorkspaceMaterializationStore",
    "WorkerWorkspaceTransportEndpoint",
    "WorkspaceBoundLocalWorker",
    "WorkspaceDataContextResolver",
    "WorkspaceLifecycleFactory",
    "worker_workspace_command_topic",
]
