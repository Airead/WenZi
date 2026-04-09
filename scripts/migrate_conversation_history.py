#!/usr/bin/env python3
"""Migrate conversation_history.jsonl to add correction_tracked field.

This script:
1. Backs up the main JSONL file and all monthly archives
2. Adds `correction_tracked` field to every record:
   - True if enhance_mode == "proofread"
   - False otherwise
3. Writes updated files back atomically

Usage:
    uv run python scripts/migrate_conversation_history.py [--data-dir PATH]

The default data directory is ~/.config/WenZi.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from glob import glob


def _default_data_dir() -> str:
    from wenzi.config import DEFAULT_DATA_DIR
    return os.path.expanduser(DEFAULT_DATA_DIR)


def _backup_file(path: str, backup_dir: str) -> str:
    """Copy a file to backup_dir, preserving its basename."""
    os.makedirs(backup_dir, exist_ok=True)
    dest = os.path.join(backup_dir, os.path.basename(path))
    shutil.copy2(path, dest)
    return dest


def _migrate_jsonl(path: str, dry_run: bool = False) -> tuple[int, int]:
    """Add correction_tracked field to all records in a JSONL file.

    Returns (total_records, updated_records).
    """
    if not os.path.exists(path):
        return 0, 0

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    total = 0
    updated = 0
    new_lines: list[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            new_lines.append("\n")
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            new_lines.append(line + "\n")
            continue

        total += 1
        if "correction_tracked" not in record:
            record["correction_tracked"] = record.get("enhance_mode") == "proofread"
            updated += 1

        new_lines.append(json.dumps(record, ensure_ascii=False) + "\n")

    if not dry_run and updated > 0:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        os.replace(tmp_path, path)

    return total, updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate conversation history to add correction_tracked field")
    parser.add_argument("--data-dir", default=_default_data_dir(), help="WenZi data directory")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without modifying files")
    args = parser.parse_args()

    data_dir = args.data_dir
    dry_run = args.dry_run

    main_jsonl = os.path.join(data_dir, "conversation_history.jsonl")
    archive_dir = os.path.join(data_dir, "conversation_history_archives")
    archive_files = sorted(glob(os.path.join(archive_dir, "*.jsonl")))

    all_files = []
    if os.path.exists(main_jsonl):
        all_files.append(main_jsonl)
    all_files.extend(archive_files)

    if not all_files:
        print(f"No conversation history files found in {data_dir}")
        sys.exit(0)

    print(f"Found {len(all_files)} file(s) to migrate:")
    for f in all_files:
        print(f"  {f}")

    # Backup
    if not dry_run:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(data_dir, f"conversation_history_backup_{timestamp}")
        print(f"\nBacking up to: {backup_dir}")
        for f in all_files:
            dest = _backup_file(f, backup_dir)
            print(f"  {os.path.basename(f)} -> {dest}")
    else:
        print("\n[DRY RUN] Skipping backup")

    # Migrate
    print("\nMigrating...")
    total_records = 0
    total_updated = 0
    for f in all_files:
        records, updated = _migrate_jsonl(f, dry_run=dry_run)
        total_records += records
        total_updated += updated
        status = f"{updated}/{records} records updated"
        if dry_run:
            status = f"[DRY RUN] {status}"
        print(f"  {os.path.basename(f)}: {status}")

    print(f"\nDone. {total_updated}/{total_records} records updated across {len(all_files)} file(s).")
    if dry_run:
        print("Run without --dry-run to apply changes.")
        return



if __name__ == "__main__":
    main()
