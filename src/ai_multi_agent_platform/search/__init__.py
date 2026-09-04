"""Platform-wide derived search and discovery contracts."""

from .checkpoint import SEARCH_INDEX_SCHEMA_VERSION, SearchIndexCheckpoint
from .indexing import document_from_resource
from .local import LocalSearchProvider
from .models import SearchDocument, SearchMode, SearchPage, SearchQuery, SearchResult
from .provider import SearchProvider
from .service import SearchService

__all__ = [
    "LocalSearchProvider",
    "SEARCH_INDEX_SCHEMA_VERSION",
    "SearchDocument",
    "SearchIndexCheckpoint",
    "SearchMode",
    "SearchPage",
    "SearchProvider",
    "SearchQuery",
    "SearchResult",
    "SearchService",
    "document_from_resource",
]
