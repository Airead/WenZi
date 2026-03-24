"""Tests for PluginInstaller — install, update, uninstall plugins."""

from __future__ import annotations

import http.server
import os
import threading

import tomllib

import pytest

from wenzi.scripting.plugin_installer import (
    PluginInstaller,
    replace_github_ref,
    resolve_ref,
)
from wenzi.scripting.plugin_meta import INSTALL_TOML, load_plugin_meta


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    """Reduce download retry delay to speed up failure tests."""
    monkeypatch.setattr(PluginInstaller, "_RETRY_DELAY", 0.01)


@pytest.fixture()
def plugins_dir(tmp_path):
    """Return a temporary plugins directory."""
    d = tmp_path / "plugins"
    d.mkdir()
    return str(d)


@pytest.fixture()
def serve_dir(tmp_path):
    """Return a temporary directory to serve files from over HTTP."""
    d = tmp_path / "serve"
    d.mkdir()
    return d


@pytest.fixture()
def http_server(serve_dir):
    """Spin up a local HTTP server serving files from *serve_dir*."""

    class SilentHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(serve_dir), **kwargs)

        def log_message(self, format, *args):  # noqa: A002
            # Suppress server logs during tests
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), SilentHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.daemon = True
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


# ---------------------------------------------------------------------------
# TestInstall
# ---------------------------------------------------------------------------


class TestInstall:
    def test_install_from_url(self, plugins_dir, serve_dir, http_server):
        """Download plugin.toml + files from URL and verify install.toml is written."""
        # Set up files in serve_dir
        (serve_dir / "myplugin").mkdir()
        (serve_dir / "myplugin" / "__init__.py").write_bytes(b"# myplugin")
        (serve_dir / "myplugin" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.myplugin"\n'
            'name = "My Plugin"\n'
            'version = "1.0.0"\n'
            'files = ["__init__.py"]\n'
        )

        installer = PluginInstaller(plugins_dir)
        install_dir = installer.install(f"{http_server}/myplugin/plugin.toml")

        # plugin.toml written
        assert os.path.isfile(os.path.join(install_dir, "plugin.toml"))
        # downloaded file written
        assert os.path.isfile(os.path.join(install_dir, "__init__.py"))
        # install.toml written
        install_toml_path = os.path.join(install_dir, INSTALL_TOML)
        assert os.path.isfile(install_toml_path)
        content = open(install_toml_path).read()
        assert "source_url" in content
        assert "1.0.0" in content

    def test_install_from_local_path(self, plugins_dir, tmp_path):
        """Install from a local file path (not HTTP)."""
        local = tmp_path / "local_plugin"
        local.mkdir()
        (local / "__init__.py").write_bytes(b"# local")
        (local / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.localplugin"\n'
            'name = "Local Plugin"\n'
            'version = "0.1.0"\n'
            'files = ["__init__.py"]\n'
        )

        installer = PluginInstaller(plugins_dir)
        install_dir = installer.install(str(local / "plugin.toml"))

        assert os.path.isfile(os.path.join(install_dir, "__init__.py"))
        assert os.path.isfile(os.path.join(install_dir, INSTALL_TOML))
        meta = load_plugin_meta(install_dir)
        assert meta.id == "com.example.localplugin"

    def test_install_rollback_on_failure(self, plugins_dir, serve_dir, http_server):
        """If a file listed in plugin.toml is missing, the install dir is cleaned up."""
        (serve_dir / "broken").mkdir()
        (serve_dir / "broken" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.broken"\n'
            'name = "Broken"\n'
            'version = "1.0.0"\n'
            'files = ["missing_file.py"]\n'  # this file does not exist
        )
        # Do NOT create missing_file.py — download should fail

        installer = PluginInstaller(plugins_dir)
        with pytest.raises(Exception):
            installer.install(f"{http_server}/broken/plugin.toml")

        # The install dir should have been rolled back (removed)
        assert not any(
            os.path.isdir(os.path.join(plugins_dir, d))
            for d in os.listdir(plugins_dir)
        )

    def test_install_failure_preserves_existing_plugin(self, plugins_dir, serve_dir, http_server):
        """Re-installing a plugin with a broken source preserves the original."""
        # First, install a working plugin
        (serve_dir / "alpha").mkdir()
        (serve_dir / "alpha" / "__init__.py").write_bytes(b"# v1 working")
        (serve_dir / "alpha" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.alpha"\n'
            'name = "Alpha"\n'
            'version = "1.0.0"\n'
            'files = ["__init__.py"]\n'
        )
        installer = PluginInstaller(plugins_dir)
        install_dir = installer.install(f"{http_server}/alpha/plugin.toml")
        assert os.path.isfile(os.path.join(install_dir, "__init__.py"))

        # Now update the source to reference a missing file
        (serve_dir / "alpha" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.alpha"\n'
            'name = "Alpha"\n'
            'version = "2.0.0"\n'
            'files = ["__init__.py", "missing.py"]\n'
        )

        # Re-install should fail
        with pytest.raises(Exception):
            installer.install(f"{http_server}/alpha/plugin.toml")

        # Original plugin files must still be intact
        assert os.path.isfile(os.path.join(install_dir, "__init__.py"))
        content = open(os.path.join(install_dir, "__init__.py"), "rb").read()
        assert content == b"# v1 working"
        # No leftover temp directories
        assert not any(
            e.startswith("_tmp_") for e in os.listdir(plugins_dir)
        )

    def test_install_dir_uses_full_bundle_id(self, plugins_dir, serve_dir, http_server):
        """Install directory name is derived from the full bundle ID."""
        (serve_dir / "myplugin").mkdir()
        (serve_dir / "myplugin" / "__init__.py").write_bytes(b"# plugin")
        (serve_dir / "myplugin" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.my-plugin"\n'
            'name = "My Plugin"\n'
            'version = "1.0.0"\n'
            'files = ["__init__.py"]\n'
        )

        installer = PluginInstaller(plugins_dir)
        install_dir = installer.install(f"{http_server}/myplugin/plugin.toml")

        assert os.path.basename(install_dir) == "com_example_my_plugin"
        assert os.path.isfile(os.path.join(install_dir, "__init__.py"))


# ---------------------------------------------------------------------------
# TestProgressCallback
# ---------------------------------------------------------------------------


class TestProgressCallback:
    def test_install_calls_progress(self, plugins_dir, serve_dir, http_server):
        """install() calls progress callback with (current, total) for each file."""
        (serve_dir / "prog").mkdir()
        (serve_dir / "prog" / "a.py").write_bytes(b"# a")
        (serve_dir / "prog" / "b.py").write_bytes(b"# b")
        (serve_dir / "prog" / "c.py").write_bytes(b"# c")
        (serve_dir / "prog" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.prog"\n'
            'name = "Prog"\n'
            'version = "1.0.0"\n'
            'files = ["a.py", "b.py", "c.py"]\n'
        )
        calls = []
        installer = PluginInstaller(plugins_dir)
        installer.install(
            f"{http_server}/prog/plugin.toml",
            progress=lambda cur, tot: calls.append((cur, tot)),
        )
        # Concurrent downloads may complete in any order, but all 3 are reported
        assert len(calls) == 3
        assert all(tot == 3 for _, tot in calls)
        assert sorted(cur for cur, _ in calls) == [1, 2, 3]

    def test_install_no_files_skips_progress(self, plugins_dir, serve_dir, http_server):
        """install() with empty files list does not call progress."""
        (serve_dir / "empty").mkdir()
        (serve_dir / "empty" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.empty"\n'
            'name = "Empty"\n'
            'version = "1.0.0"\n'
            'files = []\n'
        )
        calls = []
        installer = PluginInstaller(plugins_dir)
        installer.install(
            f"{http_server}/empty/plugin.toml",
            progress=lambda cur, tot: calls.append((cur, tot)),
        )
        assert calls == []


# ---------------------------------------------------------------------------
# TestPinnedInstall
# ---------------------------------------------------------------------------


class TestPinnedInstall:
    def test_install_with_pinned_ref_writes_field(self, plugins_dir, serve_dir, http_server):
        """install(url, pinned_ref=...) writes pinned_ref to install.toml."""
        (serve_dir / "myplugin").mkdir()
        (serve_dir / "myplugin" / "__init__.py").write_bytes(b"# plugin")
        (serve_dir / "myplugin" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.pinned"\n'
            'name = "Pinned"\n'
            'version = "1.0.0"\n'
            'files = ["__init__.py"]\n'
        )
        installer = PluginInstaller(plugins_dir)
        install_dir = installer.install(
            f"{http_server}/myplugin/plugin.toml", pinned_ref="1.0.0"
        )
        install_toml = os.path.join(install_dir, INSTALL_TOML)
        with open(install_toml, "rb") as f:
            data = tomllib.load(f)
        assert data["install"]["pinned_ref"] == "1.0.0"

    def test_install_without_pinned_ref_omits_field(self, plugins_dir, serve_dir, http_server):
        """install(url) without pinned_ref does not write pinned_ref."""
        (serve_dir / "myplugin").mkdir()
        (serve_dir / "myplugin" / "__init__.py").write_bytes(b"# plugin")
        (serve_dir / "myplugin" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.normal"\n'
            'name = "Normal"\n'
            'version = "1.0.0"\n'
            'files = ["__init__.py"]\n'
        )
        installer = PluginInstaller(plugins_dir)
        install_dir = installer.install(f"{http_server}/myplugin/plugin.toml")
        install_toml = os.path.join(install_dir, INSTALL_TOML)
        with open(install_toml, "rb") as f:
            data = tomllib.load(f)
        assert "pinned_ref" not in data["install"]


# ---------------------------------------------------------------------------
# TestUpdate
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_update_failure_preserves_existing_plugin(self, plugins_dir, serve_dir, http_server):
        """A failed update does not corrupt the existing plugin."""
        # Install v1
        (serve_dir / "beta").mkdir()
        (serve_dir / "beta" / "__init__.py").write_bytes(b"# v1")
        (serve_dir / "beta" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.beta"\n'
            'name = "Beta"\n'
            'version = "1.0.0"\n'
            'files = ["__init__.py"]\n'
        )
        installer = PluginInstaller(plugins_dir)
        install_dir = installer.install(f"{http_server}/beta/plugin.toml")

        # Change source to v2 with a missing file
        (serve_dir / "beta" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.beta"\n'
            'name = "Beta"\n'
            'version = "2.0.0"\n'
            'files = ["__init__.py", "missing.py"]\n'
        )

        with pytest.raises(Exception):
            installer.update("com.example.beta")

        # v1 files must still be intact
        content = open(os.path.join(install_dir, "__init__.py"), "rb").read()
        assert content == b"# v1"
        meta = load_plugin_meta(install_dir)
        assert meta.version == "1.0.0"
        # No leftover temp directories
        assert not any(
            e.startswith("_tmp_") for e in os.listdir(plugins_dir)
        )

    def test_update_preserves_pinned_ref(self, plugins_dir, serve_dir, http_server):
        """update() on a pinned plugin preserves pinned_ref in install.toml."""
        (serve_dir / "pinned").mkdir()
        (serve_dir / "pinned" / "__init__.py").write_bytes(b"# v1")
        (serve_dir / "pinned" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.pinned"\n'
            'name = "Pinned"\n'
            'version = "1.0.0"\n'
            'files = ["__init__.py"]\n'
        )
        installer = PluginInstaller(plugins_dir)
        install_dir = installer.install(
            f"{http_server}/pinned/plugin.toml", pinned_ref="1.0.0"
        )
        with open(os.path.join(install_dir, INSTALL_TOML), "rb") as f:
            assert tomllib.load(f)["install"]["pinned_ref"] == "1.0.0"

        installer.update("com.example.pinned")
        with open(os.path.join(install_dir, INSTALL_TOML), "rb") as f:
            data = tomllib.load(f)
        assert data["install"]["pinned_ref"] == "1.0.0"

    def test_update_overwrites_files(self, plugins_dir, serve_dir, http_server):
        """Update downloads a newer version and overwrites existing files."""
        # Initial install
        (serve_dir / "alpha").mkdir()
        (serve_dir / "alpha" / "__init__.py").write_bytes(b"# v1")
        (serve_dir / "alpha" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.alpha"\n'
            'name = "Alpha"\n'
            'version = "1.0.0"\n'
            'files = ["__init__.py"]\n'
        )

        installer = PluginInstaller(plugins_dir)
        install_dir = installer.install(f"{http_server}/alpha/plugin.toml")
        assert open(os.path.join(install_dir, "__init__.py"), "rb").read() == b"# v1"

        # Update serve_dir to v2
        (serve_dir / "alpha" / "__init__.py").write_bytes(b"# v2")
        (serve_dir / "alpha" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.alpha"\n'
            'name = "Alpha"\n'
            'version = "2.0.0"\n'
            'files = ["__init__.py"]\n'
        )

        installer.update("com.example.alpha")
        assert open(os.path.join(install_dir, "__init__.py"), "rb").read() == b"# v2"
        meta = load_plugin_meta(install_dir)
        assert meta.version == "2.0.0"


# ---------------------------------------------------------------------------
# TestUninstall
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_uninstall_removes_directory(self, plugins_dir, serve_dir, http_server):
        """Uninstall deletes the plugin directory."""
        (serve_dir / "gamma").mkdir()
        (serve_dir / "gamma" / "plugin.toml").write_text(
            '[plugin]\n'
            'id = "com.example.gamma"\n'
            'name = "Gamma"\n'
            'version = "1.0.0"\n'
            'files = []\n'
        )

        installer = PluginInstaller(plugins_dir)
        install_dir = installer.install(f"{http_server}/gamma/plugin.toml")
        assert os.path.isdir(install_dir)

        installer.uninstall("com.example.gamma")
        assert not os.path.isdir(install_dir)

    def test_uninstall_not_found_raises(self, plugins_dir):
        """Uninstalling a non-existent plugin raises ValueError."""
        installer = PluginInstaller(plugins_dir)
        with pytest.raises(ValueError, match="not found"):
            installer.uninstall("com.example.nonexistent")


_BASE = "https://raw.githubusercontent.com/Airead/WenZi"
_PATH = "plugins/cc_sessions/plugin.toml"
_DEFAULT_URL = f"{_BASE}/refs/heads/main/{_PATH}"


class TestReplaceGithubRef:
    def test_replace_with_tag(self):
        result = replace_github_ref(_DEFAULT_URL, "refs/tags/v0.1.0")
        assert result == f"{_BASE}/refs/tags/v0.1.0/{_PATH}"

    def test_replace_with_commit_sha(self):
        sha = "a" * 40
        result = replace_github_ref(_DEFAULT_URL, sha)
        assert result == f"{_BASE}/{sha}/{_PATH}"

    def test_replace_with_branch(self):
        result = replace_github_ref(_DEFAULT_URL, "refs/heads/feat/foo")
        assert result == f"{_BASE}/refs/heads/feat/foo/{_PATH}"

    def test_custom_current_ref(self):
        url = f"{_BASE}/refs/heads/develop/{_PATH}"
        result = replace_github_ref(url, "refs/tags/v1.0.0", current_ref="refs/heads/develop")
        assert result == f"{_BASE}/refs/tags/v1.0.0/{_PATH}"

    def test_non_github_url_raises(self):
        with pytest.raises(ValueError, match="GitHub raw URL"):
            replace_github_ref("https://example.com/plugin.toml", "refs/tags/v1.0")

    def test_current_ref_not_found_raises(self):
        with pytest.raises(ValueError, match="not found"):
            replace_github_ref(_DEFAULT_URL, "refs/tags/v1.0", current_ref="refs/heads/develop")

    def test_local_path_raises(self):
        with pytest.raises(ValueError, match="GitHub raw URL"):
            replace_github_ref("/tmp/plugin.toml", "refs/tags/v1.0")


class TestResolveRef:
    def test_version_number(self):
        assert resolve_ref("0.1.0") == "refs/tags/v0.1.0"

    def test_version_with_v_prefix(self):
        assert resolve_ref("v0.1.0") == "refs/tags/v0.1.0"

    def test_two_part_version(self):
        assert resolve_ref("1.0") == "refs/tags/v1.0"

    def test_commit_sha_lowercase(self):
        sha = "a" * 40
        assert resolve_ref(sha) == sha

    def test_commit_sha_uppercase(self):
        sha = "A" * 40
        assert resolve_ref(sha) == "a" * 40

    def test_branch_name(self):
        assert resolve_ref("main") == "refs/heads/main"

    def test_branch_with_slash(self):
        assert resolve_ref("feat/foo") == "refs/heads/feat/foo"

    def test_short_sha_raises(self):
        with pytest.raises(ValueError, match="full 40-character"):
            resolve_ref("abc1234")

    def test_short_sha_12_chars_raises(self):
        with pytest.raises(ValueError, match="full 40-character"):
            resolve_ref("a" * 12)

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            resolve_ref("")
