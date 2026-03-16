"""vt.snippets — snippet management API for user scripts."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SnippetsAPI:
    """CRUD operations on the snippet store, exposed as vt.snippets."""

    def __init__(self) -> None:
        self._store = None

    def _set_store(self, store) -> None:
        """Inject the SnippetStore instance (called by ScriptEngine)."""
        self._store = store

    def list(self) -> List[Dict[str, str]]:
        """Return all snippets as a list of dicts.

        Each dict has keys: ``name``, ``keyword``, ``content``, ``category``.
        Returns an empty list when the snippet store is not available.
        """
        if self._store is None:
            return []
        return self._store.snippets

    def get(self, keyword: str) -> Optional[Dict[str, str]]:
        """Find a snippet by its keyword.

        Returns the snippet dict or ``None`` if not found.
        """
        if self._store is None:
            return None
        return self._store.find_by_keyword(keyword)

    def add(
        self,
        name: str,
        keyword: str,
        content: str,
        category: str = "",
    ) -> bool:
        """Add a new snippet.

        Returns ``True`` on success, ``False`` if the keyword already exists
        or the store is not available.
        """
        if self._store is None:
            logger.warning("Snippet store not available")
            return False
        return self._store.add(
            name=name, keyword=keyword, content=content, category=category,
        )

    def remove(self, keyword: str) -> bool:
        """Remove a snippet by keyword.

        Returns ``True`` if the snippet was found and removed.
        """
        if self._store is None:
            return False
        snippet = self._store.find_by_keyword(keyword)
        if snippet is None:
            return False
        return self._store.remove(
            name=snippet["name"], category=snippet.get("category", ""),
        )

    def update(
        self,
        keyword: str,
        *,
        new_name: Optional[str] = None,
        new_keyword: Optional[str] = None,
        content: Optional[str] = None,
        new_category: Optional[str] = None,
    ) -> bool:
        """Update an existing snippet identified by its keyword.

        Only the provided keyword arguments are changed; others remain as-is.
        Returns ``True`` on success, ``False`` if not found.
        """
        if self._store is None:
            return False
        snippet = self._store.find_by_keyword(keyword)
        if snippet is None:
            return False
        return self._store.update(
            name=snippet["name"],
            category=snippet.get("category", ""),
            new_name=new_name,
            new_keyword=new_keyword,
            content=content,
            new_category=new_category,
        )
