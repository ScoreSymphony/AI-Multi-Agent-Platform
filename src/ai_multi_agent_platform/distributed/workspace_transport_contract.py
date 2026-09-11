"""Workspace transport wire-routing constants shared by control and worker endpoints."""

WORKSPACE_TRANSPORT_SCHEMA_VERSION = "1"
WORKSPACE_COMMAND_TOPIC_PREFIX = "distributed.worker.workspace.commands"
WORKSPACE_REPLY_TOPIC_PREFIX = "distributed.worker.workspace.replies"
DEFAULT_WORKSPACE_CHUNK_BYTES = 128 * 1024


def worker_workspace_command_topic(worker_id: str) -> str:
    if not worker_id.strip():
        raise ValueError("worker_id must not be blank")
    return f"{WORKSPACE_COMMAND_TOPIC_PREFIX}.{worker_id}"


__all__ = [
    "DEFAULT_WORKSPACE_CHUNK_BYTES",
    "WORKSPACE_COMMAND_TOPIC_PREFIX",
    "WORKSPACE_REPLY_TOPIC_PREFIX",
    "WORKSPACE_TRANSPORT_SCHEMA_VERSION",
    "worker_workspace_command_topic",
]
