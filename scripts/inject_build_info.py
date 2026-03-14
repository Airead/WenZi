#!/usr/bin/env python3
"""Inject git hash and build date into _build_info.py before packaging."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    project_dir = Path(__file__).resolve().parent.parent
    build_info_path = project_dir / "src" / "voicetext" / "_build_info.py"

    try:
        git_hash = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=project_dir,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        git_hash = "unknown"

    build_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    content = f'''"""Build information injected during CI/release builds."""

GIT_HASH = "{git_hash}"
BUILD_DATE = "{build_date}"
'''
    build_info_path.write_text(content)
    print(f"Injected build info: hash={git_hash}, date={build_date}")


if __name__ == "__main__":
    main()
