"""Tests for system_settings_source."""

from __future__ import annotations

from unittest.mock import patch

from wenzi.scripting.sources.system_settings_source import (
    SettingsEntry,
    SystemSettingsSource,
    build_url,
    get_static_entries,
)


class TestBuildUrl:
    def test_panel_url(self):
        url = build_url("com.apple.BluetoothSettings")
        assert url == "x-apple.systempreferences:com.apple.BluetoothSettings"

    def test_anchor_url(self):
        url = build_url(
            "com.apple.settings.PrivacySecurity.extension",
            anchor="Privacy_Camera",
        )
        assert (
            url
            == "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Camera"
        )

    def test_colon_url(self):
        url = build_url(
            "com.apple.systempreferences.AppleIDSettings",
            sub_id="icloud",
        )
        assert (
            url
            == "x-apple.systempreferences:com.apple.systempreferences.AppleIDSettings:icloud"
        )

    def test_anchor_takes_precedence_over_sub_id(self):
        url = build_url("com.apple.Foo", anchor="bar", sub_id="baz")
        assert "?bar" in url
        assert ":baz" not in url


class TestSettingsEntry:
    def test_panel_entry(self):
        entry = SettingsEntry(
            title="Bluetooth",
            pane_id="com.apple.BluetoothSettings",
        )
        assert entry.url == "x-apple.systempreferences:com.apple.BluetoothSettings"
        assert entry.breadcrumb == "Bluetooth"

    def test_subitem_entry_with_anchor(self):
        entry = SettingsEntry(
            title="Camera",
            pane_id="com.apple.settings.PrivacySecurity.extension",
            anchor="Privacy_Camera",
            parent_title="Privacy & Security",
            keywords=("camera", "webcam"),
        )
        assert "?Privacy_Camera" in entry.url
        assert entry.breadcrumb == "Privacy & Security › Camera"

    def test_subitem_entry_with_sub_id(self):
        entry = SettingsEntry(
            title="iCloud",
            pane_id="com.apple.systempreferences.AppleIDSettings",
            sub_id="icloud",
            parent_title="Apple ID",
        )
        assert ":icloud" in entry.url
        assert entry.breadcrumb == "Apple ID › iCloud"

    def test_item_id_with_anchor(self):
        entry = SettingsEntry(
            title="Camera",
            pane_id="com.apple.settings.PrivacySecurity.extension",
            anchor="Privacy_Camera",
        )
        assert entry.item_id == "system_settings:Privacy_Camera"

    def test_item_id_with_sub_id(self):
        entry = SettingsEntry(
            title="iCloud",
            pane_id="com.apple.systempreferences.AppleIDSettings",
            sub_id="icloud",
        )
        assert entry.item_id == "system_settings:icloud"

    def test_item_id_panel_only(self):
        entry = SettingsEntry(
            title="Bluetooth",
            pane_id="com.apple.BluetoothSettings",
        )
        assert entry.item_id == "system_settings:com.apple.BluetoothSettings"


class TestStaticEntries:
    def test_returns_panels_and_subitems(self):
        entries = get_static_entries()
        assert len(entries) > 40  # 27 panels + 24 privacy + 11 general + 1 icloud

    def test_top_level_panels_present(self):
        entries = get_static_entries()
        titles = {e.title for e in entries if not e.parent_title}
        assert "Wi-Fi" in titles
        assert "Bluetooth" in titles
        assert "Privacy & Security" in titles
        assert "Keyboard" in titles
        assert "Apple ID" in titles

    def test_privacy_camera_present(self):
        entries = get_static_entries()
        camera = [e for e in entries if e.anchor == "Privacy_Camera"]
        assert len(camera) == 1
        assert camera[0].parent_title == "Privacy & Security"
        assert camera[0].keywords

    def test_general_subpanels_present(self):
        entries = get_static_entries()
        about = [e for e in entries if "About" in e.title and e.parent_title == "General"]
        assert len(about) == 1

    def test_all_entries_have_pane_id(self):
        for entry in get_static_entries():
            assert entry.pane_id, f"{entry.title} missing pane_id"

    def test_all_entries_have_url(self):
        for entry in get_static_entries():
            assert entry.url.startswith("x-apple.systempreferences:")

    def test_icloud_subpane_present(self):
        entries = get_static_entries()
        icloud = [e for e in entries if e.sub_id == "icloud"]
        assert len(icloud) == 1
        assert icloud[0].parent_title == "Apple ID"

    def test_panels_have_appex_name(self):
        entries = get_static_entries()
        panels = [e for e in entries if not e.parent_title]
        for panel in panels:
            assert panel.appex_name, f"{panel.title} missing appex_name"

    def test_subitems_inherit_parent_appex_name(self):
        entries = get_static_entries()
        camera = [e for e in entries if e.anchor == "Privacy_Camera"][0]
        assert camera.appex_name == "SecurityPrivacyExtension"


class TestSystemSettingsSource:
    def _make_source(self, tmp_path):
        """Create a source with tmp extensions dir (icons won't resolve)."""
        return SystemSettingsSource(
            extensions_dir=str(tmp_path),
            icon_cache_dir=str(tmp_path / "icon_cache"),
        )

    def test_search_panel_by_name(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("bluetooth")
        assert len(results) >= 1
        assert results[0].title == "Bluetooth"

    def test_search_subitem(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("camera")
        assert len(results) >= 1
        assert any("Camera" in r.title for r in results)

    def test_search_empty_returns_panels(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("")
        panels = [r for r in results if r.subtitle == "System Settings"]
        assert len(panels) >= 20  # All top-level panels

    def test_search_no_match(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("xyznonexistent")
        assert len(results) == 0

    def test_panel_ranks_above_subitems(self, tmp_path):
        """Searching a panel name should show the panel before its sub-items."""
        src = self._make_source(tmp_path)
        results = src.search("privacy")
        assert len(results) >= 2
        assert results[0].title == "Privacy & Security"
        assert results[0].subtitle == "System Settings"

    def test_search_by_keyword(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("webcam")
        assert any("Camera" in r.title for r in results)

    def test_items_have_action(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("bluetooth")
        assert results[0].action is not None

    def test_items_have_item_id(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("bluetooth")
        assert results[0].item_id.startswith("system_settings:")

    def test_subitem_subtitle_shows_breadcrumb(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("camera")
        camera = [r for r in results if r.title == "Camera"]
        assert camera
        assert "Privacy & Security" in camera[0].subtitle

    def test_panel_subtitle_shows_system_settings(self, tmp_path):
        src = self._make_source(tmp_path)
        results = src.search("bluetooth")
        assert results[0].subtitle == "System Settings"

    def test_icon_cached_to_disk(self, tmp_path):
        """When appex exists, icon is extracted and cached as PNG."""
        ext_dir = tmp_path / "extensions"
        appex = ext_dir / "BluetoothSettings.appex"
        appex.mkdir(parents=True)
        cache_dir = tmp_path / "icon_cache"

        fake_png = b"\x89PNG-fake"
        with patch(
            "wenzi.scripting.sources.system_settings_source._get_icon_png",
            return_value=fake_png,
        ):
            src = SystemSettingsSource(
                extensions_dir=str(ext_dir),
                icon_cache_dir=str(cache_dir),
            )
            results = src.search("bluetooth")
            bt = [r for r in results if r.title == "Bluetooth"]
            assert bt
            assert bt[0].icon.startswith("file://")
            assert bt[0].icon.endswith(".png")

    def test_icon_empty_when_no_appex(self, tmp_path):
        """When appex doesn't exist, icon is empty string."""
        src = self._make_source(tmp_path)
        results = src.search("bluetooth")
        assert results[0].icon == ""

    def test_mixed_search_limits_results(self, tmp_path):
        src = self._make_source(tmp_path)
        sources = src.as_chooser_source()
        unprefixed = [s for s in sources if s.prefix is None][0]
        results = unprefixed.search("a")
        assert len(results) <= 5

    def test_action_calls_on_open(self, tmp_path, monkeypatch):
        called = []
        src = SystemSettingsSource(
            extensions_dir=str(tmp_path),
            icon_cache_dir=str(tmp_path / "ic"),
            on_open=lambda: called.append(True),
        )
        monkeypatch.setattr(
            "wenzi.scripting.sources.system_settings_source._open_url", lambda u: None
        )
        results = src.search("bluetooth")
        results[0].action()
        assert called == [True]

    def test_secondary_action_copies_url(self, tmp_path, monkeypatch):
        copied = []
        monkeypatch.setattr(
            "wenzi.scripting.sources.copy_to_clipboard",
            lambda text: copied.append(text),
        )
        src = SystemSettingsSource(
            extensions_dir=str(tmp_path),
            icon_cache_dir=str(tmp_path / "ic"),
        )
        results = src.search("bluetooth")
        results[0].secondary_action()
        assert copied == ["x-apple.systempreferences:com.apple.BluetoothSettings"]


class TestAsChooserSource:
    def test_returns_two_sources(self, tmp_path):
        src = SystemSettingsSource(extensions_dir=str(tmp_path))
        sources = src.as_chooser_source()
        assert len(sources) == 2

    def test_prefixed_source(self, tmp_path):
        src = SystemSettingsSource(extensions_dir=str(tmp_path))
        sources = src.as_chooser_source(prefix="ss")
        prefixed = [s for s in sources if s.prefix is not None]
        assert len(prefixed) == 1
        assert prefixed[0].prefix == "ss"
        assert prefixed[0].name == "system_settings"
        assert prefixed[0].search is not None
        assert prefixed[0].action_hints == {"enter": "Open", "cmd_enter": "Copy URL"}
        assert prefixed[0].description

    def test_unprefixed_source(self, tmp_path):
        src = SystemSettingsSource(extensions_dir=str(tmp_path))
        sources = src.as_chooser_source()
        unprefixed = [s for s in sources if s.prefix is None]
        assert len(unprefixed) == 1
        assert unprefixed[0].name == "system_settings_mixed"
        assert unprefixed[0].priority == -5
        assert unprefixed[0].search is not None

    def test_unprefixed_empty_query_returns_nothing(self, tmp_path):
        src = SystemSettingsSource(extensions_dir=str(tmp_path))
        sources = src.as_chooser_source()
        unprefixed = [s for s in sources if s.prefix is None][0]
        results = unprefixed.search("")
        assert len(results) == 0

    def test_custom_prefix(self, tmp_path):
        src = SystemSettingsSource(extensions_dir=str(tmp_path))
        sources = src.as_chooser_source(prefix="set")
        prefixed = [s for s in sources if s.prefix is not None]
        assert prefixed[0].prefix == "set"
