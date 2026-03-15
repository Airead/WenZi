"""vt.chooser — Chooser panel API for user scripts."""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

from voicetext.scripting.sources import ChooserItem, ChooserSource
from voicetext.scripting.ui.chooser_panel import ChooserPanel

logger = logging.getLogger(__name__)


class ChooserAPI:
    """API for the Chooser panel, exposed as vt.chooser."""

    def __init__(self) -> None:
        self._panel = ChooserPanel()

    @property
    def panel(self) -> ChooserPanel:
        """Access the underlying ChooserPanel."""
        return self._panel

    def register_source(self, source: ChooserSource) -> None:
        """Register a data source."""
        self._panel.register_source(source)

    def show(self) -> None:
        """Show the chooser panel."""
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._panel.show)
        except Exception:
            logger.exception("Failed to show chooser")

    def close(self) -> None:
        """Close the chooser panel."""
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._panel.close)
        except Exception:
            logger.exception("Failed to close chooser")

    def toggle(self) -> None:
        """Toggle the chooser panel visibility."""
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._panel.toggle)
        except Exception:
            logger.exception("Failed to toggle chooser")

    def source(
        self,
        name: str,
        prefix: Optional[str] = None,
        priority: int = 0,
    ) -> Callable:
        """Decorator to register a search function as a chooser source.

        Usage::

            @vt.chooser.source("bookmarks", prefix=">bm")
            def search_bookmarks(query):
                return [{"title": "GitHub", "subtitle": "https://github.com",
                         "action": lambda: vt.execute("open https://github.com")}]
        """

        def decorator(func: Callable[[str], List[dict]]) -> Callable:
            def _search(query: str) -> List[ChooserItem]:
                raw_items = func(query)
                return [
                    ChooserItem(
                        title=item.get("title", ""),
                        subtitle=item.get("subtitle", ""),
                        action=item.get("action"),
                        reveal_path=item.get("reveal_path"),
                    )
                    for item in (raw_items or [])
                ]

            src = ChooserSource(
                name=name,
                prefix=prefix,
                search=_search,
                priority=priority,
            )
            self._panel.register_source(src)
            logger.info("User script registered chooser source: %s", name)
            return func

        return decorator
