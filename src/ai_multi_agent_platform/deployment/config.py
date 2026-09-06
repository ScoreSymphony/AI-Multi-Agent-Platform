"""Validated deployment configuration for the single-node production profile."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.configuration import (
    ConfigLayer,
    ConfigScope,
    ConfigSource,
    ConfigurationError,
    ConfigurationResolver,
    ConfigurationSchema,
)

_SINGLE_NODE_SCHEMA = ConfigurationSchema(
    version="1",
    json_schema={
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["deployment"],
        "properties": {
            "deployment": {
                "type": "object",
                "additionalProperties": False,
                "required": ["data_dir", "host", "port", "secure_cookie", "log_level"],
                "properties": {
                    "data_dir": {"type": "string", "minLength": 1},
                    "host": {"type": "string", "minLength": 1},
                    "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "secure_cookie": {"type": "boolean"},
                    "log_level": {
                        "type": "string",
                        "enum": ["critical", "error", "warning", "info", "debug"],
                    },
                    "registry_catalog": {"type": ["string", "null"]},
                    "registry_signature_keys": {"type": ["string", "null"]},
                },
            }
        },
    },
)

_DEFAULTS = ConfigLayer(
    ConfigScope.PLATFORM_DEFAULTS,
    {
        "deployment": {
            "data_dir": ".data/single-node",
            "host": "127.0.0.1",
            "port": 8000,
            "secure_cookie": True,
            "log_level": "info",
            "registry_catalog": None,
            "registry_signature_keys": None,
        }
    },
    ConfigSource("single-node-defaults", "built-in"),
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True, slots=True)
class SingleNodeConfig:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000
    secure_cookie: bool = True
    log_level: str = "info"
    registry_catalog: Path | None = None
    registry_signature_keys: Path | None = None

    @property
    def database_dir(self) -> Path:
        return self.data_dir / "db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def repositories_dir(self) -> Path:
        """Adapter-private storage for managed local repository working trees."""

        return self.data_dir / "repositories"

    @property
    def executor_dir(self) -> Path:
        return self.data_dir / "executor"

    @property
    def evaluation_dir(self) -> Path:
        """Deployment-owned Evaluation assets; independent from optional portability."""

        return self.data_dir / "evaluation"

    @property
    def evaluation_suites_dir(self) -> Path:
        return self.evaluation_dir / "suites"

    @property
    def evaluation_regression_policies_dir(self) -> Path:
        return self.evaluation_dir / "regression-policies"

    @property
    def evaluation_aggregation_policies_dir(self) -> Path:
        return self.evaluation_dir / "aggregation-policies"

    @property
    def evaluation_fixtures_dir(self) -> Path:
        return self.evaluation_dir / "fixtures"

    def prepare_directories(self) -> None:
        for path in (
            self.data_dir,
            self.database_dir,
            self.files_dir,
            self.workspaces_dir,
            self.repositories_dir,
            self.executor_dir,
            self.evaluation_dir,
            self.evaluation_suites_dir,
            self.evaluation_regression_policies_dir,
            self.evaluation_aggregation_policies_dir,
            self.evaluation_fixtures_dir,
        ):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ConfigurationError(
                    f"single-node deployment cannot prepare required persistence path: {path}"
                ) from exc
            if not path.is_dir():
                raise ConfigurationError(
                    f"single-node deployment persistence path is not a directory: {path}"
                )


def load_single_node_config(environ: Mapping[str, str] | None = None) -> SingleNodeConfig:
    """Resolve only explicitly supported deployment environment variables through #34."""

    source = os.environ if environ is None else environ
    deployment_values: dict[str, object] = {}
    target = deployment_values.setdefault("deployment", {})
    assert isinstance(target, dict)

    if "AI_MAP_DATA_DIR" in source:
        target["data_dir"] = source["AI_MAP_DATA_DIR"]
    if "AI_MAP_HOST" in source:
        target["host"] = source["AI_MAP_HOST"]
    if "AI_MAP_PORT" in source:
        try:
            target["port"] = int(source["AI_MAP_PORT"])
        except ValueError as exc:
            raise ConfigurationError("AI_MAP_PORT must be an integer") from exc
    if "AI_MAP_SECURE_COOKIE" in source:
        target["secure_cookie"] = _parse_bool(
            source["AI_MAP_SECURE_COOKIE"], "AI_MAP_SECURE_COOKIE"
        )
    if "AI_MAP_LOG_LEVEL" in source:
        target["log_level"] = source["AI_MAP_LOG_LEVEL"].strip().lower()
    if "AI_MAP_REGISTRY_CATALOG" in source:
        target["registry_catalog"] = _optional_path_text(
            source["AI_MAP_REGISTRY_CATALOG"], "AI_MAP_REGISTRY_CATALOG"
        )
    if "AI_MAP_REGISTRY_SIGNATURE_KEYS" in source:
        target["registry_signature_keys"] = _optional_path_text(
            source["AI_MAP_REGISTRY_SIGNATURE_KEYS"], "AI_MAP_REGISTRY_SIGNATURE_KEYS"
        )

    layers = [_DEFAULTS]
    if target:
        layers.append(
            ConfigLayer(
                ConfigScope.DEPLOYMENT,
                deployment_values,
                ConfigSource("single-node-environment", "environment"),
            )
        )
    effective = ConfigurationResolver(_SINGLE_NODE_SCHEMA).resolve(tuple(layers))
    deployment = effective.values["deployment"]
    if not isinstance(deployment, Mapping):
        raise ConfigurationError("resolved deployment configuration must be an object")

    data_dir = Path(str(deployment["data_dir"])).expanduser()
    host = str(deployment["host"]).strip()
    if not host:
        raise ConfigurationError("deployment host must not be blank")
    secure_cookie = bool(deployment["secure_cookie"])
    if not secure_cookie and host.casefold() not in _LOOPBACK_HOSTS:
        raise ConfigurationError(
            "AI_MAP_SECURE_COOKIE may be disabled only for an explicit loopback-only deployment"
        )
    return SingleNodeConfig(
        data_dir=data_dir,
        host=host,
        port=int(deployment["port"]),
        secure_cookie=secure_cookie,
        log_level=str(deployment["log_level"]),
        registry_catalog=_resolved_path(deployment.get("registry_catalog")),
        registry_signature_keys=_resolved_path(deployment.get("registry_signature_keys")),
    )


def _resolved_path(value: object) -> Path | None:
    if value is None:
        return None
    return Path(str(value)).expanduser()


def _optional_path_text(value: str, name: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        raise ConfigurationError(f"{name} must be a non-blank path when set")
    return stripped


def _parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true/false")
