"""Platform-wide derived search and discovery contracts."""

from .indexing import document_from_resource
from .local import LocalSearchProvider
from .models import SearchDocument, SearchMode, SearchPage, SearchQuery, SearchResult
from .provider import SearchProvider
from .service import SearchService

__all__ = [
    "LocalSearchProvider",
    "SearchDocument",
    "SearchMode",
    "SearchPage",
    "SearchProvider",
    "SearchQuery",
    "SearchResult",
    "SearchService",
    "document_from_resource",
]
