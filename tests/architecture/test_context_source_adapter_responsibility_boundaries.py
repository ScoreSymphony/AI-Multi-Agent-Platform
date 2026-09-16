"""Architecture regressions for Context source-adapter responsibility boundaries."""

from ai_multi_agent_platform.context import (
    FileArtifactResultContextSourceAdapter as PublicFileArtifactResultContextSourceAdapter,
    RepositoryContextSourceAdapter as PublicRepositoryContextSourceAdapter,
)
from ai_multi_agent_platform.context.file_source_adapter import (
    FileArtifactResultContextSourceAdapter,
)
from ai_multi_agent_platform.context.repository_source_adapter import (
    RepositoryContextSourceAdapter,
)
from ai_multi_agent_platform.context.source_adapters import (
    FileArtifactResultContextSourceAdapter as FacadeFileArtifactResultContextSourceAdapter,
    RepositoryContextSourceAdapter as FacadeRepositoryContextSourceAdapter,
)


def test_context_source_adapter_facade_preserves_public_class_identity() -> None:
    assert PublicRepositoryContextSourceAdapter is RepositoryContextSourceAdapter
    assert FacadeRepositoryContextSourceAdapter is RepositoryContextSourceAdapter
    assert PublicFileArtifactResultContextSourceAdapter is FileArtifactResultContextSourceAdapter
    assert FacadeFileArtifactResultContextSourceAdapter is FileArtifactResultContextSourceAdapter
