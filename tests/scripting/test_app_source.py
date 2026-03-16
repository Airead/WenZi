"""Tests for the App search data source."""

import os

from unittest.mock import patch

from voicetext.scripting.sources.app_source import (
    AppSource,
    _APP_DIRS,
    _cache_key,
    _is_internal_app,
    _png_to_data_uri,
    _scan_apps,
)


class TestAppDirs:
    def test_core_services_in_app_dirs(self):
        """CoreServices root should be scanned to find Finder.app etc."""
        assert "/System/Library/CoreServices" in _APP_DIRS

    def test_core_services_applications_in_app_dirs(self):
        """CoreServices/Applications should still be scanned."""
        assert "/System/Library/CoreServices/Applications" in _APP_DIRS


class TestIsInternalApp:
    """Verify internal system app filtering logic."""

    def test_known_user_apps_not_filtered(self):
        for name in ("Finder", "Siri", "Spotlight", "Screen Time", "Dock"):
            assert not _is_internal_app(name), f"{name} should NOT be filtered"

    def test_agent_suffix_filtered(self):
        for name in ("AirPlayUIAgent", "CoreLocationAgent", "WiFiAgent"):
            assert _is_internal_app(name), f"{name} should be filtered"

    def test_server_suffix_filtered(self):
        assert _is_internal_app("AccessibilityUIServer")
        assert _is_internal_app("SystemUIServer")

    def test_helper_suffix_filtered(self):
        assert _is_internal_app("DiscHelper")
        assert _is_internal_app("ProfileHelper")

    def test_skip_names_filtered(self):
        for name in ("loginwindow", "rcd", "liquiddetectiond", "screencaptureui"):
            assert _is_internal_app(name), f"{name} should be filtered"

    def test_scan_filters_core_services_internals(self, tmp_path):
        """Internal apps should be filtered when scanning CoreServices dir."""
        (tmp_path / "Finder.app").mkdir()
        (tmp_path / "WiFiAgent.app").mkdir()
        (tmp_path / "loginwindow.app").mkdir()

        with patch(
            "voicetext.scripting.sources.app_source._APP_DIRS",
            [str(tmp_path)],
        ), patch(
            "voicetext.scripting.sources.app_source._CORE_SERVICES_DIR",
            str(tmp_path),
        ):
            apps = _scan_apps()

        names = {a["name"] for a in apps}
        assert "Finder" in names
        assert "WiFiAgent" not in names
        assert "loginwindow" not in names


class TestScanApps:
    def test_scans_directories(self, tmp_path):
        """Scan should find .app bundles in the specified directories."""
        app1 = tmp_path / "Safari.app"
        app1.mkdir()
        app2 = tmp_path / "Chrome.app"
        app2.mkdir()
        # Non-app entries should be ignored
        (tmp_path / "readme.txt").touch()

        with patch(
            "voicetext.scripting.sources.app_source._APP_DIRS",
            [str(tmp_path)],
        ):
            apps = _scan_apps()

        names = {a["name"] for a in apps}
        assert "Safari" in names
        assert "Chrome" in names
        assert len(apps) == 2

    def test_deduplicates_apps(self, tmp_path):
        """Same app name in multiple directories should appear once."""
        dir1 = tmp_path / "dir1"
        dir1.mkdir()
        dir2 = tmp_path / "dir2"
        dir2.mkdir()
        (dir1 / "Safari.app").mkdir()
        (dir2 / "Safari.app").mkdir()

        with patch(
            "voicetext.scripting.sources.app_source._APP_DIRS",
            [str(dir1), str(dir2)],
        ):
            apps = _scan_apps()

        assert len(apps) == 1

    def test_nonexistent_directory(self):
        """Non-existent directory should not cause errors."""
        with patch(
            "voicetext.scripting.sources.app_source._APP_DIRS",
            ["/nonexistent/path"],
        ):
            apps = _scan_apps()
        assert apps == []


class TestAppSource:
    def _make_source(self, tmp_path):
        """Create an AppSource with a temp directory."""
        (tmp_path / "Safari.app").mkdir()
        (tmp_path / "Slack.app").mkdir()
        (tmp_path / "WeChat.app").mkdir()
        (tmp_path / "Terminal.app").mkdir()

        with patch(
            "voicetext.scripting.sources.app_source._APP_DIRS",
            [str(tmp_path)],
        ):
            src = AppSource()
            src._ensure_scanned()
        return src

    def test_empty_query_returns_empty(self, tmp_path):
        src = self._make_source(tmp_path)
        with patch(
            "voicetext.scripting.sources.app_source._get_running_app_names",
            return_value=set(),
        ):
            result = src.search("")
        assert result == []

    def test_substring_match(self, tmp_path):
        src = self._make_source(tmp_path)
        with patch(
            "voicetext.scripting.sources.app_source._get_running_app_names",
            return_value=set(),
        ):
            result = src.search("saf")
        assert len(result) == 1
        assert result[0].title == "Safari"

    def test_case_insensitive(self, tmp_path):
        src = self._make_source(tmp_path)
        with patch(
            "voicetext.scripting.sources.app_source._get_running_app_names",
            return_value=set(),
        ):
            result = src.search("SAFARI")
        assert len(result) == 1

    def test_running_apps_first(self, tmp_path):
        src = self._make_source(tmp_path)
        with patch(
            "voicetext.scripting.sources.app_source._get_running_app_names",
            return_value={"Terminal"},
        ):
            result = src.search("t")  # Matches Terminal, WeChat
        # Terminal is running so should be first
        titles = [r.title for r in result]
        assert titles[0] == "Terminal"

    def test_running_subtitle(self, tmp_path):
        src = self._make_source(tmp_path)
        with patch(
            "voicetext.scripting.sources.app_source._get_running_app_names",
            return_value={"Safari"},
        ):
            result = src.search("saf")
        assert result[0].subtitle == "Running"

    def test_non_running_subtitle(self, tmp_path):
        src = self._make_source(tmp_path)
        with patch(
            "voicetext.scripting.sources.app_source._get_running_app_names",
            return_value=set(),
        ):
            result = src.search("saf")
        assert result[0].subtitle == "Application"

    def test_reveal_path(self, tmp_path):
        src = self._make_source(tmp_path)
        with patch(
            "voicetext.scripting.sources.app_source._get_running_app_names",
            return_value=set(),
        ):
            result = src.search("saf")
        assert result[0].reveal_path == str(tmp_path / "Safari.app")

    def test_action_callable(self, tmp_path):
        src = self._make_source(tmp_path)
        with patch(
            "voicetext.scripting.sources.app_source._get_running_app_names",
            return_value=set(),
        ):
            result = src.search("saf")
        assert result[0].action is not None
        assert callable(result[0].action)

    def test_rescan(self, tmp_path):
        src = self._make_source(tmp_path)
        (tmp_path / "NewApp.app").mkdir()
        with patch(
            "voicetext.scripting.sources.app_source._APP_DIRS",
            [str(tmp_path)],
        ):
            src.rescan()
        with patch(
            "voicetext.scripting.sources.app_source._get_running_app_names",
            return_value=set(),
        ):
            result = src.search("new")
        assert len(result) == 1

    def test_search_by_display_name(self, tmp_path):
        """Should match against localized display_name as well."""
        (tmp_path / "Notes.app").mkdir()

        with patch(
            "voicetext.scripting.sources.app_source._APP_DIRS",
            [str(tmp_path)],
        ), patch(
            "voicetext.scripting.sources.app_source._get_display_name",
            side_effect=lambda path, fallback: "备忘录" if "Notes" in path else fallback,
        ):
            src = AppSource()
            src._ensure_scanned()

        with patch(
            "voicetext.scripting.sources.app_source._get_running_app_names",
            return_value=set(),
        ):
            # Search by Chinese display name
            result = src.search("备忘")
            assert len(result) == 1
            assert result[0].title == "备忘录"

            # Search by English bundle name still works
            result = src.search("notes")
            assert len(result) == 1
            assert result[0].title == "备忘录"

    def test_running_matches_display_name(self, tmp_path):
        """Running status should match against localized name too."""
        (tmp_path / "Notes.app").mkdir()

        with patch(
            "voicetext.scripting.sources.app_source._APP_DIRS",
            [str(tmp_path)],
        ), patch(
            "voicetext.scripting.sources.app_source._get_display_name",
            side_effect=lambda path, fallback: "备忘录" if "Notes" in path else fallback,
        ):
            src = AppSource()
            src._ensure_scanned()

        with patch(
            "voicetext.scripting.sources.app_source._get_running_app_names",
            return_value={"备忘录"},
        ):
            result = src.search("notes")
            assert len(result) == 1
            assert result[0].subtitle == "Running"

    def test_as_chooser_source(self, tmp_path):
        src = self._make_source(tmp_path)
        cs = src.as_chooser_source()
        assert cs.name == "apps"
        assert cs.prefix is None
        assert cs.priority == 10
        assert cs.search is not None


class TestIconDiskCache:
    """Tests for the disk-based icon cache."""

    _FAKE_PNG = b"\x89PNG\r\n\x1a\nfake"

    def test_save_and_load(self, tmp_path):
        """Icon should be saved to disk and loaded on next access."""
        cache_dir = str(tmp_path / "icons")
        app_dir = tmp_path / "apps"
        app_dir.mkdir()
        (app_dir / "Test.app").mkdir()
        app_path = str(app_dir / "Test.app")

        src = AppSource(icon_cache_dir=cache_dir)

        with patch(
            "voicetext.scripting.sources.app_source._get_app_icon_png",
            return_value=self._FAKE_PNG,
        ):
            uri1 = src._get_icon(app_path)

        assert uri1 == _png_to_data_uri(self._FAKE_PNG)

        # Verify files written to disk
        key = _cache_key(app_path)
        assert os.path.isfile(os.path.join(cache_dir, f"{key}.png"))
        assert os.path.isfile(os.path.join(cache_dir, f"{key}.meta"))

        # New instance should load from disk without calling AppKit
        src2 = AppSource(icon_cache_dir=cache_dir)
        with patch(
            "voicetext.scripting.sources.app_source._get_app_icon_png",
        ) as mock_extract:
            uri2 = src2._get_icon(app_path)
            mock_extract.assert_not_called()

        assert uri2 == uri1

    def test_cache_invalidated_on_mtime_change(self, tmp_path):
        """Cache should be invalidated when app mtime changes."""
        cache_dir = str(tmp_path / "icons")
        app_dir = tmp_path / "apps"
        app_dir.mkdir()
        app_path_obj = app_dir / "Test.app"
        app_path_obj.mkdir()
        app_path = str(app_path_obj)

        src = AppSource(icon_cache_dir=cache_dir)

        with patch(
            "voicetext.scripting.sources.app_source._get_app_icon_png",
            return_value=self._FAKE_PNG,
        ):
            src._get_icon(app_path)

        # Simulate app update by changing mtime
        new_mtime = os.path.getmtime(app_path) + 100
        os.utime(app_path, (new_mtime, new_mtime))

        new_png = b"\x89PNG\r\n\x1a\nnew_icon"
        src2 = AppSource(icon_cache_dir=cache_dir)
        with patch(
            "voicetext.scripting.sources.app_source._get_app_icon_png",
            return_value=new_png,
        ):
            uri = src2._get_icon(app_path)

        assert uri == _png_to_data_uri(new_png)

    def test_missing_cache_dir_created(self, tmp_path):
        """Cache dir should be auto-created on first save."""
        cache_dir = str(tmp_path / "nonexistent" / "icons")
        app_dir = tmp_path / "apps"
        app_dir.mkdir()
        (app_dir / "Test.app").mkdir()
        app_path = str(app_dir / "Test.app")

        src = AppSource(icon_cache_dir=cache_dir)
        with patch(
            "voicetext.scripting.sources.app_source._get_app_icon_png",
            return_value=self._FAKE_PNG,
        ):
            src._get_icon(app_path)

        assert os.path.isdir(cache_dir)

    def test_no_icon_returns_empty(self, tmp_path):
        """AppKit returning None should cache empty string."""
        cache_dir = str(tmp_path / "icons")
        app_dir = tmp_path / "apps"
        app_dir.mkdir()
        (app_dir / "Test.app").mkdir()
        app_path = str(app_dir / "Test.app")

        src = AppSource(icon_cache_dir=cache_dir)
        with patch(
            "voicetext.scripting.sources.app_source._get_app_icon_png",
            return_value=None,
        ):
            uri = src._get_icon(app_path)

        assert uri == ""
        # Should not write cache files for failed extraction
        key = _cache_key(app_path)
        assert not os.path.isfile(os.path.join(cache_dir, f"{key}.png"))

    def test_memory_cache_avoids_disk_read(self, tmp_path):
        """Second call should use in-memory cache, not disk."""
        cache_dir = str(tmp_path / "icons")
        app_dir = tmp_path / "apps"
        app_dir.mkdir()
        (app_dir / "Test.app").mkdir()
        app_path = str(app_dir / "Test.app")

        src = AppSource(icon_cache_dir=cache_dir)
        with patch(
            "voicetext.scripting.sources.app_source._get_app_icon_png",
            return_value=self._FAKE_PNG,
        ):
            uri1 = src._get_icon(app_path)

        # Patch disk load to verify it's not called on second access
        with patch.object(src, "_load_icon_from_disk") as mock_disk:
            uri2 = src._get_icon(app_path)
            mock_disk.assert_not_called()

        assert uri1 == uri2
