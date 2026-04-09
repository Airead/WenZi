"""Tests for XDG directory migration (config → data/cache split)."""

from __future__ import annotations

from unittest.mock import patch


class TestMigrateXdgPaths:
    """Test migrate_xdg_paths() moves files from config to data/cache dirs."""

    def test_data_files_migrated(self, tmp_path):
        """Data files are moved from config dir to data dir."""
        config = tmp_path / "config"
        data = tmp_path / "data"
        config.mkdir()

        # Create data files in old config location
        (config / "conversation_history.jsonl").write_text("{}")
        (config / "clipboard_history.db").write_text("")
        (config / "clipboard_history.json").write_text("[]")
        (config / "usage_stats.json").write_text("{}")
        (config / "chooser_usage.json").write_text("{}")
        (config / "script_data.json").write_text("{}")

        (config / "clipboard_images").mkdir()
        (config / "clipboard_images" / "img.png").write_text("")

        (config / "conversation_history_archives").mkdir()
        (config / "conversation_history_archives" / "2025-01.jsonl").write_text("")

        (config / "usage_stats").mkdir()
        (config / "usage_stats" / "2025-01-01.json").write_text("{}")

        from wenzi.config import migrate_xdg_paths

        with patch("wenzi.config.DEFAULT_CONFIG_DIR", str(config)), \
             patch("wenzi.config.DEFAULT_DATA_DIR", str(data)), \
             patch("wenzi.config.DEFAULT_CACHE_DIR", str(tmp_path / "cache")):
            migrate_xdg_paths()

        # Files should be in data dir
        assert (data / "conversation_history.jsonl").exists()
        assert (data / "clipboard_history.db").exists()
        assert (data / "clipboard_history.json").exists()

        assert (data / "usage_stats.json").exists()
        assert (data / "chooser_usage.json").exists()
        assert (data / "script_data.json").exists()
        assert (data / "clipboard_images" / "img.png").exists()
        assert (data / "conversation_history_archives" / "2025-01.jsonl").exists()
        assert (data / "usage_stats" / "2025-01-01.json").exists()

    def test_cache_files_migrated(self, tmp_path):
        """Cache files are moved from config dir to cache dir."""
        config = tmp_path / "config"
        cache = tmp_path / "cache"
        config.mkdir()

        (config / "icon_cache").mkdir()
        (config / "icon_cache" / "abc.png").write_text("")
        (config / "_chooser.html").write_text("")

        from wenzi.config import migrate_xdg_paths

        with patch("wenzi.config.DEFAULT_CONFIG_DIR", str(config)), \
             patch("wenzi.config.DEFAULT_DATA_DIR", str(tmp_path / "data")), \
             patch("wenzi.config.DEFAULT_CACHE_DIR", str(cache)):
            migrate_xdg_paths()

        assert (cache / "icon_cache" / "abc.png").exists()
        assert (cache / "_chooser.html").exists()

    def test_no_overwrite_existing(self, tmp_path):
        """Existing files at the destination are not overwritten."""
        config = tmp_path / "config"
        data = tmp_path / "data"
        config.mkdir()
        data.mkdir()

        (config / "usage_stats.json").write_text("old")
        (data / "usage_stats.json").write_text("new")

        from wenzi.config import migrate_xdg_paths

        with patch("wenzi.config.DEFAULT_CONFIG_DIR", str(config)), \
             patch("wenzi.config.DEFAULT_DATA_DIR", str(data)), \
             patch("wenzi.config.DEFAULT_CACHE_DIR", str(tmp_path / "cache")):
            migrate_xdg_paths()

        # Destination keeps its content
        assert (data / "usage_stats.json").read_text() == "new"
        # Source still exists (rename was skipped)
        assert (config / "usage_stats.json").exists()

    def test_missing_source_is_harmless(self, tmp_path):
        """Migration is a no-op when source files don't exist."""
        config = tmp_path / "config"
        config.mkdir()

        from wenzi.config import migrate_xdg_paths

        with patch("wenzi.config.DEFAULT_CONFIG_DIR", str(config)), \
             patch("wenzi.config.DEFAULT_DATA_DIR", str(tmp_path / "data")), \
             patch("wenzi.config.DEFAULT_CACHE_DIR", str(tmp_path / "cache")):
            migrate_xdg_paths()

        # No directories created when nothing to migrate
        assert not (tmp_path / "data").exists()
        assert not (tmp_path / "cache").exists()

    def test_config_files_stay_in_config_dir(self, tmp_path):
        """Config files are not touched during migration."""
        config = tmp_path / "config"
        config.mkdir()

        (config / "config.json").write_text("{}")
        (config / "enhance_modes").mkdir()
        (config / "scripts").mkdir()
        (config / "snippets").mkdir()
        (config / "sounds").mkdir()

        from wenzi.config import migrate_xdg_paths

        with patch("wenzi.config.DEFAULT_CONFIG_DIR", str(config)), \
             patch("wenzi.config.DEFAULT_DATA_DIR", str(tmp_path / "data")), \
             patch("wenzi.config.DEFAULT_CACHE_DIR", str(tmp_path / "cache")):
            migrate_xdg_paths()

        assert (config / "config.json").exists()
        assert (config / "enhance_modes").exists()
        assert (config / "scripts").exists()
        assert (config / "snippets").exists()
        assert (config / "sounds").exists()

    def test_idempotent_second_run(self, tmp_path):
        """Running migration twice does not duplicate or fail."""
        config = tmp_path / "config"
        data = tmp_path / "data"
        config.mkdir()

        (config / "usage_stats.json").write_text("v1")

        from wenzi.config import migrate_xdg_paths

        with patch.dict("wenzi.config.__dict__", {
            "DEFAULT_CONFIG_DIR": str(config),
            "DEFAULT_DATA_DIR": str(data),
            "DEFAULT_CACHE_DIR": str(tmp_path / "cache"),
        }):
            migrate_xdg_paths()
            # Re-create source (first run renamed it away)
            (config / "usage_stats.json").write_text("v2")
            # Second run should skip because destination already exists
            migrate_xdg_paths()

        # Destination keeps v1 from first migration, not v2
        assert (data / "usage_stats.json").read_text() == "v1"

    def test_cross_device_fallback(self, tmp_path):
        """When rename fails, falls back to copy and keeps the source."""
        config = tmp_path / "config"
        data = tmp_path / "data"
        config.mkdir()

        (config / "usage_stats.json").write_text("hello")

        # Simulate cross-device rename failure
        import os as _os

        from wenzi.config import migrate_xdg_paths
        _orig_rename = _os.rename

        def _failing_rename(src, dst, *a, **kw):
            # Only fail for our test file, let other renames through
            if "usage_stats.json" in str(src):
                raise OSError(18, "Invalid cross-device link")
            return _orig_rename(src, dst, *a, **kw)

        with patch("wenzi.config.DEFAULT_CONFIG_DIR", str(config)), \
             patch("wenzi.config.DEFAULT_DATA_DIR", str(data)), \
             patch("wenzi.config.DEFAULT_CACHE_DIR", str(tmp_path / "cache")), \
             patch("os.rename", side_effect=_failing_rename):
            migrate_xdg_paths()

        # File was copied to destination
        assert (data / "usage_stats.json").read_text() == "hello"
        # Source is kept (copy fallback does not delete)
        assert (config / "usage_stats.json").exists()


class TestMigrateFile:
    """Test _migrate_file() helper."""

    def test_creates_destination_dir(self, tmp_path):
        """Destination directory is created if it doesn't exist."""
        src = tmp_path / "src"
        dst = tmp_path / "a" / "b" / "dst"
        src.mkdir()
        (src / "test.txt").write_text("hello")

        from wenzi.config import _migrate_file

        _migrate_file(str(src), str(dst), "test.txt")

        assert (dst / "test.txt").read_text() == "hello"

    def test_skips_non_file(self, tmp_path):
        """Directories are not migrated by _migrate_file."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        (src / "subdir").mkdir()

        from wenzi.config import _migrate_file

        _migrate_file(str(src), str(dst), "subdir")

        assert not dst.exists()
        assert (src / "subdir").exists()


class TestMigrateDirSafe:
    """Test _migrate_dir_safe() helper."""

    def test_moves_directory_tree(self, tmp_path):
        """Directory and its contents are moved to destination."""
        src = tmp_path / "src"
        dst = tmp_path / "parent" / "dst"
        src.mkdir()
        (src / "a.txt").write_text("aaa")
        (src / "sub").mkdir()
        (src / "sub" / "b.txt").write_text("bbb")

        from wenzi.config import _migrate_dir_safe

        _migrate_dir_safe(str(src), str(dst), "test")

        assert (dst / "a.txt").read_text() == "aaa"
        assert (dst / "sub" / "b.txt").read_text() == "bbb"

    def test_skips_when_destination_exists(self, tmp_path):
        """Does not copy if destination already exists."""
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "a.txt").write_text("new")
        (dst / "a.txt").write_text("existing")

        from wenzi.config import _migrate_dir_safe

        _migrate_dir_safe(str(src), str(dst), "test")

        assert (dst / "a.txt").read_text() == "existing"

    def test_skips_when_source_missing(self, tmp_path):
        """No error when source directory does not exist."""
        from wenzi.config import _migrate_dir_safe

        _migrate_dir_safe(str(tmp_path / "nonexistent"), str(tmp_path / "dst"), "test")

        assert not (tmp_path / "dst").exists()
