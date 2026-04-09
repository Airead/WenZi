#!/usr/bin/env python3
"""Generate plugins/registry.toml from plugins/*/plugin.toml files.

Usage:
    python scripts/sync_registry.py [--plugins-dir DIR] [--base-url URL]
"""

from __future__ import annotations

import argparse
import os
import tomllib

DEFAULT_PLUGINS_DIR = os.path.join(os.path.dirname(__file__), "..", "plugins")
DEFAULT_BASE_URL = (
    "https://raw.githubusercontent.com/Airead/WenZi/refs/heads/main/plugins"
)

REGISTRY_FIELDS = ["id", "name", "description", "version", "author", "min_wenzi_version"]


def generate_registry(
    plugins_dir: str,
    output_path: str,
    base_url: str,
) -> None:
    """Scan plugin dirs and write registry.toml."""
    plugins_dir = os.path.normpath(plugins_dir)
    entries: list[dict] = []

    for entry in sorted(os.listdir(plugins_dir)):
        entry_path = os.path.join(plugins_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        toml_path = os.path.join(entry_path, "plugin.toml")
        if not os.path.isfile(toml_path):
            continue

        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        section = data.get("plugin")
        if not isinstance(section, dict):
            print(f"WARNING: {toml_path} has no [plugin] section, skipping")
            continue

        plugin_id = section.get("id", "")
        if not plugin_id:
            print(f"WARNING: {toml_path} has no id field, skipping")
            continue

        source = f"{base_url}/{entry}/plugin.toml"
        info = {"source": source}
        for field in REGISTRY_FIELDS:
            val = section.get(field, "")
            if val:
                info[field] = str(val)
        entries.append(info)

    lines = ['name = "WenZi Official"', ""]
    for info in entries:
        lines.append("[[plugins]]")
        for key in REGISTRY_FIELDS:
            if key in info:
                lines.append(f'{key} = "{info[key]}"')
        lines.append(f'source = "{info["source"]}"')
        lines.append("")

    with open(output_path, "w") as f:
        f.write("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync plugins/registry.toml")
    parser.add_argument(
        "--plugins-dir",
        default=os.path.normpath(DEFAULT_PLUGINS_DIR),
        help="Path to plugins directory",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base URL for plugin.toml source links",
    )
    args = parser.parse_args()
    output = os.path.join(args.plugins_dir, "registry.toml")
    generate_registry(args.plugins_dir, output, args.base_url)
    print(f"Registry written to {output}")


if __name__ == "__main__":
    main()
