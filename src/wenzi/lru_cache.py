"""Simple LRU cache based on OrderedDict."""

from __future__ import annotations

from collections import OrderedDict
from typing import TypeVar

K = TypeVar("K")
V = TypeVar("V")


class LRUCache(OrderedDict):
    """Bounded LRU cache that evicts the least-recently-used entry when full.

    Supports the same API as a regular dict with additional ``maxsize``
    semantics.  Accessing a key via ``get`` or ``__getitem__`` promotes it
    to the most-recently-used position.
    """

    def __init__(self, maxsize: int = 128) -> None:
        super().__init__()
        if maxsize < 1:
            raise ValueError(f"maxsize must be >= 1, got {maxsize}")
        self._maxsize = maxsize

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def __getitem__(self, key: K) -> V:
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def get(self, key: K, default: V | None = None) -> V | None:  # type: ignore[override]
        if key in self:
            return self[key]
        return default

    def __setitem__(self, key: K, value: V) -> None:
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self._maxsize:
            self.popitem(last=False)
