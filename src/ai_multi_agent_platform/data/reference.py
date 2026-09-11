"""Compatibility façade for the local data-provider reference implementations.

Issue #723 decomposes the former all-purpose module by canonical provider responsibility.
Keep these imports stable for existing callers and for the lifecycle-capable provider layer;
new implementation work belongs in the focused provider modules.
"""

from .reference_file import LocalFileProvider
from .reference_knowledge import LocalKnowledgeProvider
from .reference_memory import LocalMemoryProvider

__all__ = [
    "LocalFileProvider",
    "LocalKnowledgeProvider",
    "LocalMemoryProvider",
]
