"""Configuration-driven executor selection."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .contracts import Executor


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}

    def register(self, name: str, executor: Executor) -> None:
        if not name.strip():
            raise ValueError("executor name must not be blank")
        current = self._executors.get(name)
        if current is executor:
            return
        if current is not None:
            raise ContractError(ErrorCode.CONFLICT, f"executor is already registered: {name}")
        self._executors[name] = executor

    def unregister(self, name: str) -> Executor:
        try:
            return self._executors.pop(name)
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"executor is not registered: {name}") from exc

    @property
    def executor_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._executors))

    def select(self, name: str) -> Executor:
        try:
            return self._executors[name]
        except KeyError as exc:
            raise KeyError(f"unknown executor: {name}") from exc

    @classmethod
    def from_config(
        cls,
        config: dict[str, Executor],
        *,
        default: str | None = None,
    ) -> tuple[ExecutorRegistry, Executor | None]:
        registry = cls()
        for name, executor in config.items():
            registry.register(name, executor)
        return registry, registry.select(default) if default is not None else None
