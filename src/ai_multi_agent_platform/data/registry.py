"""Dependency-injection hook for the data providers used by the control plane."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import FileProvider, KnowledgeProvider, MemoryProvider


@dataclass(frozen=True, slots=True)
class DataProviderSet:
    """Replaceable provider bundle; no concrete backend is imported by callers."""

    files: FileProvider
    memory: MemoryProvider
    knowledge: KnowledgeProvider
