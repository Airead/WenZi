"""Command source for the Chooser.

Scripts register named commands via ``wz.chooser.register_command()`` or the
``@wz.chooser.command()`` decorator.  The user activates the command palette
by typing ``> `` in the launcher and can fuzzy-search or pass arguments.

Args mode: when the first word of the query *exactly* matches a command name
and is followed by a space, the remainder is treated as arguments passed to the
command's action callback.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from wenzi.scripting.sources import (
    ChooserItem,
    ChooserSource,
    ModifierAction,
    fuzzy_match_fields,
)

logger = logging.getLogger(__name__)

_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_:-]*$")

COMMAND_PREFIX = ">"


@dataclass
class CommandEntry:
    """A registered command."""

    name: str  # Unique identifier, single token (e.g. "reload-scripts")
    title: str  # Display title
    subtitle: str = ""
    icon: str = ""
    action: Callable[[str], None] | None = field(default=None, repr=False)
    modifiers: dict[str, ModifierAction] | None = field(
        default=None, repr=False,
    )
    promoted: bool = False  # Also appear in unprefixed main search
    universal_action: bool = False  # Opt-in to Universal Action mode


class CommandSource:
    """Manages a registry of named commands and provides chooser search."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandEntry] = {}

    # ── Registration ──────────────────────────────────────────────

    def register(self, entry: CommandEntry) -> None:
        """Register a command.  Overwrites if the name already exists."""
        if not _VALID_NAME_RE.match(entry.name):
            raise ValueError(
                f"Invalid command name {entry.name!r}: must be alphanumeric, "
                "hyphens, underscores, or colons (no spaces)."
            )
        if entry.name in self._commands:
            logger.warning(
                "Command %r re-registered, overwriting previous", entry.name,
            )
        self._commands[entry.name] = entry
        logger.info("Command registered: %s", entry.name)

    def unregister(self, name: str) -> None:
        """Remove a command by name.  No-op if not found."""
        if self._commands.pop(name, None) is not None:
            logger.info("Command unregistered: %s", name)

    def clear(self) -> None:
        """Remove all registered commands."""
        self._commands.clear()

    # ── Search ────────────────────────────────────────────────────

    def search(self, query: str) -> list[ChooserItem]:
        """Search registered commands.

        * Empty query → return all commands sorted by name.
        * First word exactly matches a command name + space → args mode:
          return that single command with args captured in the action closure.
        * Otherwise → fuzzy-match the query against all command titles/names.
        """
        query = query.lstrip()

        if not query:
            return [
                self._make_item(cmd, "")
                for cmd in sorted(self._commands.values(), key=lambda c: c.name)
            ]

        # Args mode: first word exact match + space separator
        if " " in query:
            name_part, args = query.split(" ", 1)
            cmd = self._commands.get(name_part)
            if cmd is not None:
                return [self._make_item(cmd, args)]

        # Fuzzy search mode
        scored: list[tuple] = []
        for cmd in self._commands.values():
            matched, score = fuzzy_match_fields(query, (cmd.title, cmd.name))
            if matched:
                scored.append((-score, cmd.name, cmd))

        scored.sort()
        return [self._make_item(cmd, "") for _, _, cmd in scored]

    # ── Tab completion ────────────────────────────────────────────

    def complete(self, query: str, item: ChooserItem) -> str | None:
        """Tab-complete the selected command name.

        Returns the completed query (without prefix) with a trailing space
        to enter args mode, or ``None`` if completion is not applicable.
        """
        # Extract command name from item_id (prefixed with "cmd:")
        cmd_name = (item.item_id or "").removeprefix("cmd:")
        if cmd_name and cmd_name in self._commands:
            return cmd_name + " "
        return None

    # ── Promoted (unprefixed) search ─────────────────────────────

    def promoted_search(self, query: str) -> list[ChooserItem]:
        """Search only promoted commands (no args mode, no prefix needed)."""
        promoted = [c for c in self._commands.values() if c.promoted]
        if not promoted:
            return []

        if not query.strip():
            return []

        scored: list[tuple] = []
        for cmd in promoted:
            matched, score = fuzzy_match_fields(query, (cmd.title, cmd.name))
            if matched:
                scored.append((-score, cmd.name, cmd))

        scored.sort()
        return [self._make_item(cmd, "") for _, _, cmd in scored]

    # ── ChooserSource factory ─────────────────────────────────────

    def as_chooser_source(self) -> ChooserSource:
        """Return a :class:`ChooserSource` wired to this command registry."""
        from wenzi.i18n import t

        return ChooserSource(
            name="commands",
            display_name=t("chooser.source.commands"),
            prefix=COMMAND_PREFIX,
            search=self.search,
            complete=self.complete,
            priority=8,
            description="Command palette",
            action_hints={
                "enter": t("chooser.action.run"),
                "tab": t("chooser.action.complete"),
            },
        )

    def as_promoted_chooser_source(self) -> ChooserSource:
        """Return an unprefixed :class:`ChooserSource` for promoted commands."""
        from wenzi.i18n import t

        return ChooserSource(
            name="commands-promoted",
            prefix=None,
            search=self.promoted_search,
            priority=6,
            action_hints={
                "enter": t("chooser.action.run"),
            },
        )

    # ── Internal ──────────────────────────────────────────────────

    def _make_item(self, cmd: CommandEntry, args: str) -> ChooserItem:
        """Build a :class:`ChooserItem` for a command, capturing *args*."""
        subtitle = cmd.subtitle
        if args:
            subtitle = f"args: {args}" if not cmd.subtitle else f"{cmd.subtitle}  ·  args: {args}"

        # Build modifier actions that also receive args
        modifiers: dict[str, ModifierAction] | None = None
        if cmd.modifiers:
            modifiers = {}
            for key, mod in cmd.modifiers.items():
                captured_args = args

                def _mod_action(a=captured_args, fn=mod.action):
                    if fn is not None:
                        fn(a)

                modifiers[key] = ModifierAction(
                    subtitle=mod.subtitle,
                    action=_mod_action,
                )

        captured_args = args

        def _action(a=captured_args, fn=cmd.action):
            if fn is not None:
                fn(a)

        return ChooserItem(
            title=cmd.title,
            subtitle=subtitle,
            icon=cmd.icon,
            item_id=f"cmd:{cmd.name}",
            action=_action,
            modifiers=modifiers,
        )
