"""Provider-neutral advanced deployment profiles for issue #240.

The profile layer deliberately contains deployment metadata separately from the
canonical #14 Node/Worker records it materializes. It does not introduce a
second scheduler, Worker identity model, transport contract or secret format.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from ai_multi_agent_platform.distributed import (
    AcceleratorResource,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
)
from ai_multi_agent_platform.security import SecretReference

ConnectionMode = Literal["local", "remote"]
NetworkScope = Literal["loopback", "private", "public"]

_FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "credential",
        "credential_value",
        "password",
        "private_key",
        "secret_value",
        "token",
    }
)


class AdvancedDeploymentProfileError(ValueError):
    """Raised when an advanced deployment profile violates #240 boundaries."""


@dataclass(frozen=True, slots=True)
class ControlPlaneBinding:
    """Deployment-only location metadata for the canonical Control Plane."""

    endpoint_ref: str
    network_scope: NetworkScope
    tls_required: bool


@dataclass(frozen=True, slots=True)
class WorkerHostBinding:
    """Machine-local metadata that must never become canonical scheduling identity."""

    host_ref: str
    connection_mode: ConnectionMode
    transport_endpoint_ref: str
    workspace_root: Path
    tls_required: bool
    credential_reference: SecretReference | None = None


@dataclass(frozen=True, slots=True)
class OptionalServiceBinding:
    """Replaceable optional service boundary used only by deployment composition."""

    service_id: str
    enabled: bool
    network_scope: NetworkScope = "private"
    endpoint_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.service_id.strip():
            raise AdvancedDeploymentProfileError("optional service_id must not be blank")
        if self.enabled and self.endpoint_ref is None:
            raise AdvancedDeploymentProfileError(
                f"enabled optional service {self.service_id!r} requires endpoint_ref"
            )
        if not self.enabled and self.endpoint_ref is not None:
            raise AdvancedDeploymentProfileError(
                f"disabled optional service {self.service_id!r} must not expose endpoint_ref"
            )


@dataclass(frozen=True, slots=True)
class DeploymentNode:
    """One deployment binding plus the canonical Node/Workers declared for that host."""

    binding: WorkerHostBinding
    node: NodeRecord
    workers: tuple[WorkerRecord, ...]
    reporter_worker_id: str | None = None

    def __post_init__(self) -> None:
        worker_ids = {worker.worker_id for worker in self.workers}
        if len(worker_ids) != len(self.workers):
            raise AdvancedDeploymentProfileError("deployment node contains duplicate Worker IDs")
        if any(worker.node_id != self.node.node_id for worker in self.workers):
            raise AdvancedDeploymentProfileError(
                "deployment Worker node_id must match its canonical Node"
            )
        if self.binding.connection_mode == "remote":
            if self.reporter_worker_id is None or self.reporter_worker_id not in worker_ids:
                raise AdvancedDeploymentProfileError(
                    "remote deployment node requires a reporter_worker_id from its Worker set"
                )
            if self.binding.credential_reference is None:
                raise AdvancedDeploymentProfileError(
                    "remote deployment node requires a canonical SecretReference"
                )
            if not self.binding.tls_required:
                raise AdvancedDeploymentProfileError(
                    "remote deployment node must require authenticated TLS transport"
                )
        elif self.reporter_worker_id is not None and self.reporter_worker_id not in worker_ids:
            raise AdvancedDeploymentProfileError(
                "local reporter_worker_id must refer to a declared Worker"
            )

    def registration_request(self) -> RegistrationRequest:
        """Create the canonical #14 registration payload without deployment metadata."""

        return RegistrationRequest(
            node=self.node,
            workers=self.workers,
            service_identity_ref=(
                self.reporter_worker_id if self.binding.connection_mode == "remote" else None
            ),
        )


@dataclass(frozen=True, slots=True)
class AdvancedDeploymentProfile:
    """Validated #240 composition over canonical distributed runtime contracts."""

    profile_id: str
    description: str
    control_plane: ControlPlaneBinding
    nodes: tuple[DeploymentNode, ...]
    optional_services: tuple[OptionalServiceBinding, ...] = ()
    schema_version: str = "1"

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise AdvancedDeploymentProfileError("profile_id must not be blank")
        if not self.description.strip():
            raise AdvancedDeploymentProfileError("profile description must not be blank")
        if self.schema_version != "1":
            raise AdvancedDeploymentProfileError(
                f"unsupported advanced deployment profile schema: {self.schema_version!r}"
            )
        node_ids = [item.node.node_id for item in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise AdvancedDeploymentProfileError("profile contains duplicate canonical Node IDs")
        worker_ids = [worker.worker_id for item in self.nodes for worker in item.workers]
        if len(set(worker_ids)) != len(worker_ids):
            raise AdvancedDeploymentProfileError("profile contains duplicate canonical Worker IDs")
        host_refs = [item.binding.host_ref for item in self.nodes]
        if len(set(host_refs)) != len(host_refs):
            raise AdvancedDeploymentProfileError("profile contains duplicate deployment host_ref")
        service_ids = [item.service_id for item in self.optional_services]
        if len(set(service_ids)) != len(service_ids):
            raise AdvancedDeploymentProfileError("profile contains duplicate optional service_id")

    @property
    def registration_requests(self) -> tuple[RegistrationRequest, ...]:
        return tuple(item.registration_request() for item in self.nodes)


def load_advanced_deployment_profile(path: str | Path) -> AdvancedDeploymentProfile:
    """Load a credential-free #240 profile from JSON."""

    profile_path = Path(path)
    try:
        raw: object = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdvancedDeploymentProfileError(
            f"cannot load advanced deployment profile: {profile_path}"
        ) from exc
    return parse_advanced_deployment_profile(raw)


def parse_advanced_deployment_profile(value: object) -> AdvancedDeploymentProfile:
    """Validate and materialize deployment metadata plus canonical #14 declarations."""

    _reject_embedded_secrets(value)
    data = _mapping(value, "advanced deployment profile")
    _only_keys(
        data,
        {
            "schema_version",
            "profile_id",
            "description",
            "control_plane",
            "nodes",
            "optional_services",
        },
        "advanced deployment profile",
    )
    nodes_raw = _sequence(data.get("nodes"), "nodes")
    services_raw = _sequence(data.get("optional_services", []), "optional_services")
    return AdvancedDeploymentProfile(
        schema_version=_string(data.get("schema_version"), "schema_version"),
        profile_id=_string(data.get("profile_id"), "profile_id"),
        description=_string(data.get("description"), "description"),
        control_plane=_parse_control_plane(data.get("control_plane")),
        nodes=tuple(_parse_deployment_node(item) for item in nodes_raw),
        optional_services=tuple(_parse_optional_service(item) for item in services_raw),
    )


def _parse_control_plane(value: object) -> ControlPlaneBinding:
    data = _mapping(value, "control_plane")
    _only_keys(data, {"endpoint_ref", "network_scope", "tls_required"}, "control_plane")
    scope = _network_scope(data.get("network_scope"), "control_plane.network_scope")
    tls_required = _boolean(data.get("tls_required"), "control_plane.tls_required")
    if scope == "public" and not tls_required:
        raise AdvancedDeploymentProfileError("public Control Plane endpoints must require TLS")
    return ControlPlaneBinding(
        endpoint_ref=_string(data.get("endpoint_ref"), "control_plane.endpoint_ref"),
        network_scope=scope,
        tls_required=tls_required,
    )


def _parse_deployment_node(value: object) -> DeploymentNode:
    data = _mapping(value, "node entry")
    _only_keys(data, {"deployment", "canonical", "workers", "reporter_worker_id"}, "node entry")
    binding = _parse_host_binding(data.get("deployment"))
    node = _parse_node_record(data.get("canonical"))
    workers_raw = _sequence(data.get("workers"), "workers")
    workers = tuple(_parse_worker_record(item, node.node_id) for item in workers_raw)
    reporter = _optional_string(data.get("reporter_worker_id"), "reporter_worker_id")
    return DeploymentNode(
        binding=binding,
        node=node,
        workers=workers,
        reporter_worker_id=reporter,
    )


def _parse_host_binding(value: object) -> WorkerHostBinding:
    data = _mapping(value, "node deployment metadata")
    _only_keys(
        data,
        {
            "host_ref",
            "connection_mode",
            "transport_endpoint_ref",
            "workspace_root",
            "tls_required",
            "credential_reference",
        },
        "node deployment metadata",
    )
    mode = _connection_mode(data.get("connection_mode"), "deployment.connection_mode")
    credential_raw = data.get("credential_reference")
    credential = None if credential_raw is None else _parse_secret_reference(credential_raw)
    return WorkerHostBinding(
        host_ref=_string(data.get("host_ref"), "deployment.host_ref"),
        connection_mode=mode,
        transport_endpoint_ref=_string(
            data.get("transport_endpoint_ref"), "deployment.transport_endpoint_ref"
        ),
        workspace_root=Path(_string(data.get("workspace_root"), "deployment.workspace_root")),
        tls_required=_boolean(data.get("tls_required"), "deployment.tls_required"),
        credential_reference=credential,
    )


def _parse_secret_reference(value: object) -> SecretReference:
    data = _mapping(value, "credential_reference")
    _only_keys(data, {"provider", "secret_id", "scope", "version", "metadata"}, "credential_reference")
    metadata_raw = data.get("metadata", {})
    metadata = _mapping(metadata_raw, "credential_reference.metadata")
    return SecretReference(
        provider=_string(data.get("provider"), "credential_reference.provider"),
        secret_id=_string(data.get("secret_id"), "credential_reference.secret_id"),
        scope=_string(data.get("scope"), "credential_reference.scope"),
        version=_optional_string(data.get("version"), "credential_reference.version"),
        metadata=cast(Mapping[str, object], metadata),
    )


def _parse_node_record(value: object) -> NodeRecord:
    data = _mapping(value, "canonical Node")
    _only_keys(
        data,
        {
            "node_id",
            "display_name",
            "labels",
            "os_name",
            "platform",
            "architecture",
            "supported_runtimes",
            "model_refs",
            "capability_refs",
            "locality_refs",
            "resources",
        },
        "canonical Node",
    )
    return NodeRecord(
        node_id=_string(data.get("node_id"), "canonical.node_id"),
        display_name=_string(data.get("display_name"), "canonical.display_name"),
        labels=_string_tuple(data.get("labels", []), "canonical.labels"),
        os_name=_optional_string(data.get("os_name"), "canonical.os_name"),
        platform=_optional_string(data.get("platform"), "canonical.platform"),
        architecture=_optional_string(data.get("architecture"), "canonical.architecture"),
        supported_runtimes=_string_tuple(
            data.get("supported_runtimes", []), "canonical.supported_runtimes"
        ),
        model_refs=_string_tuple(data.get("model_refs", []), "canonical.model_refs"),
        capability_refs=_string_tuple(
            data.get("capability_refs", []), "canonical.capability_refs"
        ),
        locality_refs=_string_tuple(data.get("locality_refs", []), "canonical.locality_refs"),
        resources=_parse_resources(data.get("resources", {})),
    )


def _parse_worker_record(value: object, node_id: str) -> WorkerRecord:
    data = _mapping(value, "canonical Worker")
    _only_keys(
        data,
        {
            "worker_id",
            "worker_type",
            "supported_executors",
            "capability_refs",
            "supported_runtimes",
            "model_refs",
            "concurrency_limit",
            "locality_refs",
            "worker_version",
        },
        "canonical Worker",
    )
    return WorkerRecord(
        worker_id=_string(data.get("worker_id"), "worker.worker_id"),
        node_id=node_id,
        worker_type=_string(data.get("worker_type", "execution"), "worker.worker_type"),
        supported_executors=_string_tuple(
            data.get("supported_executors", []), "worker.supported_executors"
        ),
        capability_refs=_string_tuple(data.get("capability_refs", []), "worker.capability_refs"),
        supported_runtimes=_string_tuple(
            data.get("supported_runtimes", []), "worker.supported_runtimes"
        ),
        model_refs=_string_tuple(data.get("model_refs", []), "worker.model_refs"),
        concurrency_limit=_integer(
            data.get("concurrency_limit", 1), "worker.concurrency_limit", minimum=1
        ),
        locality_refs=_string_tuple(data.get("locality_refs", []), "worker.locality_refs"),
        worker_version=_string(data.get("worker_version", "0"), "worker.worker_version"),
    )


def _parse_resources(value: object) -> ResourceSnapshot:
    data = _mapping(value, "canonical resources")
    _only_keys(
        data,
        {"cpu_cores", "ram_bytes", "storage_bytes", "accelerators"},
        "canonical resources",
    )
    cpu = _number(data.get("cpu_cores", 0.0), "resources.cpu_cores", minimum=0.0)
    ram = _integer(data.get("ram_bytes", 0), "resources.ram_bytes", minimum=0)
    storage = _integer(data.get("storage_bytes", 0), "resources.storage_bytes", minimum=0)
    accelerators_raw = _sequence(data.get("accelerators", []), "resources.accelerators")
    accelerators = tuple(_parse_accelerator(item) for item in accelerators_raw)
    return ResourceSnapshot(
        cpu_cores_total=cpu,
        cpu_cores_available=cpu,
        ram_total_bytes=ram,
        ram_available_bytes=ram,
        storage_total_bytes=storage,
        storage_available_bytes=storage,
        accelerators=accelerators,
    )


def _parse_accelerator(value: object) -> AcceleratorResource:
    data = _mapping(value, "accelerator")
    _only_keys(data, {"accelerator_id", "kind", "vendor", "model", "memory_bytes"}, "accelerator")
    memory = _integer(data.get("memory_bytes", 0), "accelerator.memory_bytes", minimum=0)
    return AcceleratorResource(
        accelerator_id=_string(data.get("accelerator_id"), "accelerator.accelerator_id"),
        kind=_string(data.get("kind", "gpu"), "accelerator.kind"),
        vendor=_optional_string(data.get("vendor"), "accelerator.vendor"),
        model=_optional_string(data.get("model"), "accelerator.model"),
        memory_total_bytes=memory,
        memory_available_bytes=memory,
    )


def _parse_optional_service(value: object) -> OptionalServiceBinding:
    data = _mapping(value, "optional service")
    _only_keys(
        data,
        {"service_id", "enabled", "network_scope", "endpoint_ref"},
        "optional service",
    )
    return OptionalServiceBinding(
        service_id=_string(data.get("service_id"), "optional_service.service_id"),
        enabled=_boolean(data.get("enabled"), "optional_service.enabled"),
        network_scope=_network_scope(
            data.get("network_scope", "private"), "optional_service.network_scope"
        ),
        endpoint_ref=_optional_string(data.get("endpoint_ref"), "optional_service.endpoint_ref"),
    )


def _reject_embedded_secrets(value: object, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            if key.casefold() in _FORBIDDEN_SECRET_KEYS:
                raise AdvancedDeploymentProfileError(
                    f"plaintext secret field {key!r} is forbidden in deployment profiles at {path}"
                )
            _reject_embedded_secrets(item, path=f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _reject_embedded_secrets(item, path=f"{path}[{index}]")


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise AdvancedDeploymentProfileError(f"{label} must be an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise AdvancedDeploymentProfileError(f"{label} keys must be strings")
        result[key] = item
    return result


def _sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise AdvancedDeploymentProfileError(f"{label} must be an array")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdvancedDeploymentProfileError(f"{label} must be a non-blank string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    items = _sequence(value, label)
    return tuple(_string(item, f"{label}[]") for item in items)


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise AdvancedDeploymentProfileError(f"{label} must be a boolean")
    return value


def _integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdvancedDeploymentProfileError(f"{label} must be an integer")
    if value < minimum:
        raise AdvancedDeploymentProfileError(f"{label} must be >= {minimum}")
    return value


def _number(value: object, label: str, *, minimum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AdvancedDeploymentProfileError(f"{label} must be numeric")
    result = float(value)
    if result < minimum:
        raise AdvancedDeploymentProfileError(f"{label} must be >= {minimum}")
    return result


def _connection_mode(value: object, label: str) -> ConnectionMode:
    mode = _string(value, label)
    if mode not in {"local", "remote"}:
        raise AdvancedDeploymentProfileError(f"{label} must be 'local' or 'remote'")
    return cast(ConnectionMode, mode)


def _network_scope(value: object, label: str) -> NetworkScope:
    scope = _string(value, label)
    if scope not in {"loopback", "private", "public"}:
        raise AdvancedDeploymentProfileError(
            f"{label} must be 'loopback', 'private' or 'public'"
        )
    return cast(NetworkScope, scope)


def _only_keys(data: Mapping[str, object], allowed: set[str], label: str) -> None:
    unexpected = sorted(set(data) - allowed)
    if unexpected:
        raise AdvancedDeploymentProfileError(
            f"{label} contains unsupported fields: {', '.join(unexpected)}"
        )
