"""Tests for the file search data source."""

import os
from unittest.mock import patch

from wenzi.scripting.sources.file_source import (
    FileSource,
    FolderSource,
    _file_type_label,
    _icon_png_path_for_ext,
    _icon_png_path_for_folder,
    _mdfind,
)


class TestFileTypeLabel:
    def test_folder(self):
        with patch("os.path.isdir", return_value=True):
            assert _file_type_label("/some/folder") == "Folder"

    def test_known_extensions(self):
        with patch("os.path.isdir", return_value=False):
            assert _file_type_label("doc.pdf") == "PDF"
            assert _file_type_label("code.py") == "Python"
            assert _file_type_label("pic.png") == "Image"
            assert _file_type_label("app.app") == "Application"
            assert _file_type_label("data.json") == "JSON"

    def test_unknown_extension(self):
        with patch("os.path.isdir", return_value=False):
            assert _file_type_label("file.xyz") == "File"


class TestMdfind:
    def test_returns_paths(self):
        paths = ["/Users/test/readme.md", "/Users/test/README.txt"]
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=paths,
        ):
            result = _mdfind("readme")
            assert len(result) == 2
            assert result[0] == "/Users/test/readme.md"

    def test_empty_result(self):
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=[],
        ):
            result = _mdfind("nonexistent")
            assert result == []

    def test_max_results_passed_through(self):
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=[],
        ) as mock_search:
            _mdfind("file", max_results=5)
            mock_search.assert_called_once_with("file", 5, content_type=None)

    def test_content_type_passed_through(self):
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=[],
        ) as mock_search:
            _mdfind("file", content_type="public.folder")
            mock_search.assert_called_once_with(
                "file", 30, content_type="public.folder",
            )


class TestFileSource:
    def _make_source(self, tmp_path):
        """Create a FileSource with a temp icon cache dir."""
        return FileSource(icon_cache_dir=str(tmp_path / "icons"))

    def test_empty_query_returns_empty(self, tmp_path):
        source = self._make_source(tmp_path)
        assert source.search("") == []
        assert source.search("   ") == []

    def test_search_returns_items(self, tmp_path):
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=["/Users/test/readme.md"],
        ), patch("os.path.exists", return_value=True):
            source = self._make_source(tmp_path)
            items = source.search("readme")
            assert len(items) == 1
            assert items[0].title == "readme.md"
            assert items[0].reveal_path == "/Users/test/readme.md"
            assert items[0].action is not None

    def test_search_excludes_folders(self, tmp_path):
        """FileSource should pass content_type='!public.folder' to mdfind."""
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=[],
        ) as mock_search:
            source = self._make_source(tmp_path)
            source.search("test")
            mock_search.assert_called_once_with(
                "test", 30, content_type="!public.folder",
            )

    def test_nonexistent_paths_filtered(self, tmp_path):
        def exists_side_effect(path):
            return path == "/exists/file.txt"

        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=["/gone/file.txt", "/exists/file.txt"],
        ), patch("os.path.exists", side_effect=exists_side_effect):
            source = self._make_source(tmp_path)
            items = source.search("file")
            assert len(items) == 1
            assert items[0].title == "file.txt"

    def test_home_dir_shortened(self, tmp_path):
        home = os.path.expanduser("~")
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=[f"{home}/Documents/test.txt"],
        ), patch("os.path.exists", return_value=True):
            source = self._make_source(tmp_path)
            items = source.search("test")
            assert "~/Documents" in items[0].subtitle

    def test_as_chooser_source(self, tmp_path):
        source = self._make_source(tmp_path)
        cs = source.as_chooser_source()
        assert cs.name == "files"
        assert cs.prefix == "f"
        assert cs.priority == 3
        assert cs.search is not None
        assert "shift" in cs.action_hints

    def test_preview_is_path_type(self, tmp_path):
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=["/Users/test/file.txt"],
        ), patch("os.path.exists", return_value=True):
            source = self._make_source(tmp_path)
            items = source.search("file")
            assert items[0].preview["type"] == "path"
            assert items[0].preview["content"] == "/Users/test/file.txt"


class TestFolderSource:
    def _make_source(self, tmp_path):
        """Create a FolderSource with a temp icon cache dir."""
        return FolderSource(icon_cache_dir=str(tmp_path / "icons"))

    def test_empty_query_returns_empty(self, tmp_path):
        source = self._make_source(tmp_path)
        assert source.search("") == []

    def test_search_only_folders(self, tmp_path):
        """FolderSource should pass content_type='public.folder' to mdfind."""
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=[],
        ) as mock_search:
            source = self._make_source(tmp_path)
            source.search("doc")
            mock_search.assert_called_once_with(
                "doc", 30, content_type="public.folder",
            )

    def test_search_returns_items(self, tmp_path):
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=["/Users/test/Documents"],
        ), patch("os.path.exists", return_value=True):
            source = self._make_source(tmp_path)
            items = source.search("doc")
            assert len(items) == 1
            assert items[0].title == "Documents"
            assert "Folder" in items[0].subtitle
            assert items[0].reveal_path == "/Users/test/Documents"
            assert items[0].item_id == "folder:/Users/test/Documents"

    def test_as_chooser_source(self, tmp_path):
        source = self._make_source(tmp_path)
        cs = source.as_chooser_source()
        assert cs.name == "folders"
        assert cs.prefix == "fd"
        assert cs.priority == 3
        assert "shift" in cs.action_hints

    def test_folder_icon_cache_hit(self, tmp_path):
        """Cached folder icon should return file:// URL."""
        icon_dir = tmp_path / "icons"
        icon_dir.mkdir()
        folder_path = "/Users/test/Documents"
        png_path = _icon_png_path_for_folder(str(icon_dir), folder_path)
        with open(png_path, "wb") as f:
            f.write(b"\x89PNGfake")

        source = FolderSource(icon_cache_dir=str(icon_dir))
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=[folder_path],
        ), patch("os.path.exists", return_value=True):
            items = source.search("doc")

        assert len(items) == 1
        assert items[0].icon == "file://" + png_path

    def test_folder_icon_cache_miss_returns_empty(self, tmp_path):
        """Missing folder icon should return empty string."""
        source = FolderSource(icon_cache_dir=str(tmp_path / "icons"))
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=["/Users/test/SomeFolder"],
        ), patch("os.path.exists", return_value=True):
            items = source.search("some")

        assert len(items) == 1
        assert items[0].icon == ""


class TestFileIconCache:
    """Tests for file icon caching."""

    def test_ext_icon_cache_hit(self, tmp_path):
        """Cached extension icon should return file:// URL."""
        icon_dir = tmp_path / "icons"
        icon_dir.mkdir()
        png_path = _icon_png_path_for_ext(str(icon_dir), ".pdf")
        with open(png_path, "wb") as f:
            f.write(b"\x89PNGfake")

        source = FileSource(icon_cache_dir=str(icon_dir))
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=["/Users/test/doc.pdf"],
        ), patch("os.path.exists", return_value=True), patch(
            "os.path.isdir", return_value=False,
        ):
            items = source.search("doc")

        assert len(items) == 1
        assert items[0].icon == "file://" + png_path

    def test_ext_icon_cache_miss_returns_empty(self, tmp_path):
        """Missing extension icon should return empty string."""
        source = FileSource(icon_cache_dir=str(tmp_path / "icons"))
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=["/Users/test/doc.xyz"],
        ), patch("os.path.exists", return_value=True), patch(
            "os.path.isdir", return_value=False,
        ):
            items = source.search("doc")

        assert len(items) == 1
        assert items[0].icon == ""

    def test_app_icon_from_app_source_cache(self, tmp_path):
        """A .app file should reuse app_source's icon cache."""
        from wenzi.scripting.sources.app_source import _cache_key

        icon_dir = tmp_path / "icons"
        icon_dir.mkdir()
        app_path = "/Applications/Safari.app"
        key = _cache_key(app_path)
        png_path = os.path.join(str(icon_dir), f"{key}.png")
        with open(png_path, "wb") as f:
            f.write(b"\x89PNGfake")

        source = FileSource(icon_cache_dir=str(icon_dir))
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=[app_path],
        ), patch("os.path.exists", return_value=True), patch(
            "os.path.isdir", return_value=False,
        ):
            items = source.search("safari")

        assert len(items) == 1
        assert items[0].icon == "file://" + png_path

    def test_schedule_deduplicates_ext(self, tmp_path):
        """Same extension should only be scheduled once."""
        source = FileSource(icon_cache_dir=str(tmp_path / "icons"))
        with patch(
            "wenzi.scripting.sources.file_source.threading.Thread",
        ) as mock_thread:
            # _prewarm_common_extensions also calls Thread, reset
            mock_thread.reset_mock()
            source._schedule_ext_extraction(".xyz")
            source._schedule_ext_extraction(".xyz")
            # Only one thread spawned (the second call is deduped)
            assert mock_thread.call_count == 1

    def test_no_ext_uses_public_data(self, tmp_path):
        """Files without extension should use 'public.data' key."""
        icon_dir = tmp_path / "icons"
        icon_dir.mkdir()
        # Pre-create the public.data icon
        png_path = _icon_png_path_for_ext(str(icon_dir), "public.data")
        with open(png_path, "wb") as f:
            f.write(b"\x89PNGfake")

        source = FileSource(icon_cache_dir=str(icon_dir))
        with patch(
            "wenzi.scripting.sources.file_source.mdquery_search",
            return_value=["/Users/test/Makefile"],
        ), patch("os.path.exists", return_value=True), patch(
            "os.path.isdir", return_value=False,
        ):
            items = source.search("make")

        assert len(items) == 1
        assert items[0].icon == "file://" + png_path
