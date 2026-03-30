"""Menu Search plugin — search and trigger menu items of the frontmost app."""

from __future__ import annotations


def setup(wz) -> None:
    """Register a chooser source that searches app and WenZi menus."""

    @wz.chooser.source(
        "menu_search",
        prefix="m",
        priority=5,
        description="Search Menu Items",
        action_hints={"enter": "Trigger"},
    )
    def search(query: str) -> list:
        # Capture pid NOW while _previous_app is still valid.
        # By the time the action runs, chooser.close() will have
        # cleared _previous_app.
        pid = wz.menu._get_previous_pid()

        # Primary: frontmost app's menu bar (via Accessibility API)
        app_items = wz.menu.app_menu(pid=pid) if pid else []
        # Secondary: WenZi's own statusbar menu
        wz_items = wz.menu.list(flat=True)

        results = []
        results.extend(_search_app_items(wz, query, app_items, pid))
        results.extend(_search_wz_items(wz, query, wz_items))
        return results


def _search_app_items(wz, query: str, items: list, pid: int) -> list:
    """Build chooser results from app menu items."""
    from wenzi.scripting.sources import fuzzy_match

    if query.strip():
        scored = []
        for item in items:
            matched, score = fuzzy_match(query, item["title"])
            if not matched:
                matched, score = fuzzy_match(query, item["path"])
            if matched:
                scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        items = [item for _, item in scored]

    results = []
    for item in items:
        subtitle = item.get("shortcut", "")
        if item.get("path", "") != item["title"]:
            path = item.get("path", "")
            subtitle = f"{path}  {subtitle}".strip() if subtitle else path
        results.append({
            "title": item["title"],
            "subtitle": subtitle,
            "action": _make_app_trigger(wz, item, pid),
        })
    return results


def _search_wz_items(wz, query: str, items: list) -> list:
    """Build chooser results from WenZi's own menu items."""
    from wenzi.scripting.sources import fuzzy_match

    if query.strip():
        scored = []
        for item in items:
            matched, score = fuzzy_match(query, item["title"])
            if not matched and item.get("path"):
                matched, score = fuzzy_match(query, item["path"])
            if matched:
                scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        items = [item for _, item in scored]

    results = []
    for item in items:
        if not item.get("has_action"):
            continue
        subtitle = item.get("path", "")
        if subtitle == item["title"]:
            subtitle = ""
        entry = {
            "title": item["title"],
            "subtitle": f"WenZi  {subtitle}".strip() if subtitle else "WenZi",
            "action": _make_wz_trigger(wz, item.get("path") or item["title"]),
        }
        if item.get("state"):
            entry["icon_badge"] = "✓"
        results.append(entry)
    return results


def _make_app_trigger(wz, item: dict, pid: int):
    """Return a closure that triggers an app menu item via AX API."""
    def _trigger():
        wz.menu.app_menu_trigger(item, pid=pid)
    return _trigger


def _make_wz_trigger(wz, path: str):
    """Return a closure that triggers a WenZi menu item."""
    def _trigger():
        wz.menu.trigger(path)
    return _trigger
