"""Tests for version and build info consistency."""

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_pyproject_version() -> str:
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


class TestVersion:
    def test_version_importable(self):
        from voicetext import __version__

        assert __version__ is not None
        assert isinstance(__version__, str)
        assert len(__version__) > 0

    def test_version_matches_pyproject(self):
        """__version__ should match pyproject.toml when package is installed."""
        from voicetext import __version__

        # In dev (editable install), these should match
        # If PackageNotFoundError was raised, __version__ is "0.0.0-dev"
        if __version__ != "0.0.0-dev":
            expected = _read_pyproject_version()
            assert __version__ == expected
