"""Tests for the App search data source."""

from unittest.mock import patch

from voicetext.scripting.sources.app_source import AppSource, _scan_apps


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

    def test_as_chooser_source(self, tmp_path):
        src = self._make_source(tmp_path)
        cs = src.as_chooser_source()
        assert cs.name == "apps"
        assert cs.prefix is None
        assert cs.priority == 10
        assert cs.search is not None
