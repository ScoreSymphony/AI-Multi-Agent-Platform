"""Configuration-driven executor selection."""

from __future__ import annotations

from .contracts import Executor


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}

    def register(self, name: str, executor: Executor) -> None:
        if not name.strip():
            raise ValueError("executor name must not be blank")
        self._executors[name] = executor

    def select(self, name: str) -> Executor:
        try:
            return self._executors[name]
        except KeyError as exc:
            raise KeyError(f"unknown executor: {name}") from exc

    @classmethod
    def from_config(cls, config: dict[str, Executor], *, default: str | None = None) -> tuple["ExecutorRegistry", Executor | None]:
        registry = cls()
        for name, executor in config.items():
            registry.register(name, executor)
        return registry, registry.select(default) if default is not None else None
