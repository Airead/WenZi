"""wz.snippets — snippet management API for user scripts."""

from __future__ import annotations

import builtins
import logging

logger = logging.getLogger(__name__)


class SnippetsAPI:
    """CRUD operations on the snippet store, exposed as wz.snippets."""

    def __init__(self) -> None:
        self._store = None

    def _set_store(self, store) -> None:
        """Inject the SnippetStore instance (called by ScriptEngine)."""
        self._store = store

    def list(self) -> builtins.list[dict[str, str]]:
        """Return all snippets as a list of dicts.

        Each dict has keys: ``name``, ``keyword``, ``content``, ``category``.
        Returns an empty list when the snippet store is not available.
        """
        if self._store is None:
            return []
        return self._store.snippets

    def get(self, keyword: str) -> dict[str, str] | None:
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
        auto_expand: bool = True,
        *,
        random: bool = False,
        variants: builtins.list[str] | None = None,
    ) -> bool:
        """Add a new snippet.

        Returns ``True`` on success, ``False`` if the keyword already exists
        or the store is not available.

        Set *random* to ``True`` and pass *variants* to create a random
        snippet that picks a variant on each expansion.
        """
        if self._store is None:
            logger.warning("Snippet store not available")
            return False
        return self._store.add(
            name=name, keyword=keyword, content=content, category=category,
            auto_expand=auto_expand, random=random, variants=variants,
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
        new_name: str | None = None,
        new_keyword: str | None = None,
        content: str | None = None,
        new_category: str | None = None,
        new_auto_expand: bool | None = None,
        new_random: bool | None = None,
        new_variants: builtins.list[str] | None = None,
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
            new_auto_expand=new_auto_expand,
            new_random=new_random,
            new_variants=new_variants,
        )
