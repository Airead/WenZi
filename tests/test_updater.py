"""Tests for WenZi auto-update module (wenzi.updater)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wenzi.updater import AppUpdater, UpdateError


class TestAppUpdaterInit:
    def test_init(self):
        updater = AppUpdater(
            dmg_url="https://example.com/WenZi-1.0.0.dmg",
            version="1.0.0",
        )
        assert updater.dmg_url == "https://example.com/WenZi-1.0.0.dmg"
        assert updater.version == "1.0.0"
        assert updater._cancelled is False

    def test_cancel(self):
        updater = AppUpdater(dmg_url="https://x.com/a.dmg", version="1.0")
        updater.cancel()
        assert updater._cancelled is True


class TestGetAppBundlePath:
    def test_returns_path_from_nsbundle(self):
        mock_bundle = MagicMock()
        mock_bundle.bundlePath.return_value = "/Applications/WenZi.app"
        mock_ns_bundle = MagicMock()
        mock_ns_bundle.mainBundle.return_value = mock_bundle

        with patch.dict(
            "sys.modules", {"AppKit": MagicMock(NSBundle=mock_ns_bundle)}
        ):
            result = AppUpdater.get_app_bundle_path()
            assert result == Path("/Applications/WenZi.app")


    def test_env_override(self):
        with patch.dict("os.environ", {"WENZI_APP_PATH": "/tmp/TestWenZi.app"}):
            result = AppUpdater.get_app_bundle_path()
            assert result == Path("/tmp/TestWenZi.app")


class TestIsWritable:
    def test_writable(self, tmp_path):
        app_path = tmp_path / "WenZi.app"
        app_path.mkdir()
        assert AppUpdater.is_writable(app_path) is True

    def test_not_writable(self):
        app_path = Path("/System/WenZi.app")
        assert AppUpdater.is_writable(app_path) is False


class TestCleanupStagedApp:
    @patch("wenzi.updater.AppUpdater.get_app_bundle_path")
    def test_removes_leftover(self, mock_path, tmp_path):
        mock_path.return_value = tmp_path / "WenZi.app"
        staged = tmp_path / ".WenZi-update.app"
        staged.mkdir()
        (staged / "Contents").mkdir()

        AppUpdater.cleanup_staged_app()
        assert not staged.exists()

    @patch("wenzi.updater.AppUpdater.get_app_bundle_path")
    def test_no_leftover(self, mock_path, tmp_path):
        mock_path.return_value = tmp_path / "WenZi.app"
        AppUpdater.cleanup_staged_app()  # should not raise


class TestFindAppInVolume:
    def test_default_name(self, tmp_path):
        app = tmp_path / "WenZi.app"
        app.mkdir()
        assert AppUpdater._find_app_in_volume(tmp_path) == app

    def test_other_name(self, tmp_path):
        app = tmp_path / "SomeOther.app"
        app.mkdir()
        assert AppUpdater._find_app_in_volume(tmp_path) == app

    def test_not_found(self, tmp_path):
        with pytest.raises(UpdateError, match="not found"):
            AppUpdater._find_app_in_volume(tmp_path)


class TestMountDmg:
    @patch("wenzi.updater.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "/dev/disk5\tApple_partition_scheme\t\n"
                "/dev/disk5s1\tApple_partition_map\t\n"
                "/dev/disk5s2\tApple_HFS\t/tmp/dmg-XXXX\n"
            ),
        )
        updater = AppUpdater(dmg_url="https://x.com/a.dmg", version="1.0")

        with patch.object(Path, "is_dir", return_value=True):
            result = updater._mount_dmg(Path("/tmp/test.dmg"))
        assert result == Path("/tmp/dmg-XXXX")

    @patch("wenzi.updater.subprocess.run")
    def test_skips_empty_mount_points(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "/dev/disk5\tApple_partition_scheme\t\n"
                "/dev/disk5s1\tApple_partition_map\t\n"
                "/dev/disk5s2\tApple_HFS\t/tmp/dmg-REAL\n"
            ),
        )
        updater = AppUpdater(dmg_url="https://x.com/a.dmg", version="1.0")

        with patch.object(Path, "is_dir", return_value=True):
            result = updater._mount_dmg(Path("/tmp/test.dmg"))
        assert str(result) == "/tmp/dmg-REAL"

    @patch("wenzi.updater.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="hdiutil: attach failed",
        )
        updater = AppUpdater(dmg_url="https://x.com/a.dmg", version="1.0")
        with pytest.raises(UpdateError, match="Failed to mount"):
            updater._mount_dmg(Path("/tmp/test.dmg"))


class TestUnmountDmg:
    @patch("wenzi.updater.subprocess.run")
    def test_calls_hdiutil_detach(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        updater = AppUpdater(dmg_url="https://x.com/a.dmg", version="1.0")
        updater._unmount_dmg(Path("/tmp/dmg-mount"))

        args = mock_run.call_args[0][0]
        assert "hdiutil" in args
        assert "detach" in args
        assert "-force" in args


class TestDownloadDmg:
    @patch("wenzi.updater.urllib.request.urlopen")
    def test_download_with_progress(self, mock_urlopen, tmp_path):
        chunk_data = b"x" * 1024
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Length": str(len(chunk_data))}
        mock_resp.read.side_effect = [chunk_data, b""]
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        progress_calls = []
        updater = AppUpdater(
            dmg_url="https://x.com/a.dmg",
            version="1.0",
            on_progress=lambda msg: progress_calls.append(msg),
        )

        dest = tmp_path / "test.dmg"
        updater._download_dmg(dest)

        assert dest.exists()
        assert dest.read_bytes() == chunk_data
        assert any("100%" in msg for msg in progress_calls)

    @patch("wenzi.updater.urllib.request.urlopen")
    def test_download_cancelled(self, mock_urlopen, tmp_path):
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Length": "1024"}
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        call_count = 0

        def read_side_effect(size):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"x" * 512
            # After first chunk, cancel
            updater.cancel()
            return b"y" * 512

        mock_resp.read.side_effect = read_side_effect
        mock_urlopen.return_value = mock_resp

        updater = AppUpdater(dmg_url="https://x.com/a.dmg", version="1.0")
        dest = tmp_path / "test.dmg"
        updater._download_dmg(dest)

        # First chunk was written before cancel check
        assert dest.exists()


class TestVerifyApp:
    @patch("wenzi.updater.subprocess.run")
    def test_valid_signature(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        # Should not raise
        AppUpdater._verify_app(Path("/tmp/WenZi.app"))
        args = mock_run.call_args[0][0]
        assert "codesign" in args
        assert "--verify" in args

    @patch("wenzi.updater.subprocess.run")
    def test_invalid_signature_raises(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=1, stderr="invalid signature"
        )
        staged = tmp_path / "Staged.app"
        staged.mkdir()
        with pytest.raises(UpdateError, match="Code signature verification failed"):
            AppUpdater._verify_app(staged)
        # Should also clean up the invalid app
        assert not staged.exists()

    @patch("wenzi.updater.subprocess.run")
    def test_incomplete_copy_raises(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=3, stderr="resource envelope is obsolete"
        )
        staged = tmp_path / "Staged.app"
        staged.mkdir()
        with pytest.raises(UpdateError, match="verification failed"):
            AppUpdater._verify_app(staged)

    @patch("wenzi.updater.subprocess.run")
    def test_timeout_raises(self, mock_run, tmp_path):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="codesign", timeout=30)
        staged = tmp_path / "Staged.app"
        staged.mkdir()
        with pytest.raises(UpdateError, match="timed out"):
            AppUpdater._verify_app(staged)
        assert not staged.exists()


class TestPerformSwapAndRelaunch:
    @patch("wenzi.updater.subprocess.Popen")
    @patch("wenzi.updater.AppUpdater.get_app_bundle_path")
    def test_spawns_script(self, mock_path, mock_popen, tmp_path):
        app_path = tmp_path / "WenZi.app"
        app_path.mkdir()
        staged = tmp_path / ".WenZi-update.app"
        staged.mkdir()
        mock_path.return_value = app_path

        result = AppUpdater.perform_swap_and_relaunch()
        assert result is True

        mock_popen.assert_called_once()
        args = mock_popen.call_args
        assert args[0][0][0] == "bash"
        assert args[0][0][1] == "-c"
        assert args[1]["start_new_session"] is True

        script = args[0][0][2]
        assert str(app_path) in script
        assert str(staged) in script
        # Verify rename-swap pattern (backup before replace)
        assert ".bak" in script
        assert "xattr -rd com.apple.quarantine" in script
        assert f"open {app_path}" in script

    @patch("wenzi.updater.AppUpdater.get_app_bundle_path")
    def test_no_staged_app(self, mock_path, tmp_path):
        mock_path.return_value = tmp_path / "WenZi.app"
        result = AppUpdater.perform_swap_and_relaunch()
        assert result is False


class TestRunFullFlow:
    @patch("wenzi.updater.AppUpdater._verify_app")
    @patch("wenzi.updater.AppUpdater._unmount_dmg")
    @patch("wenzi.updater.AppUpdater._mount_dmg")
    @patch("wenzi.updater.AppUpdater._download_dmg")
    @patch("wenzi.updater.AppUpdater.is_writable", return_value=True)
    @patch("wenzi.updater.AppUpdater.get_app_bundle_path")
    def test_full_flow(
        self,
        mock_path,
        mock_writable,
        mock_download,
        mock_mount,
        mock_unmount,
        mock_verify,
        tmp_path,
    ):
        app_path = tmp_path / "WenZi.app"
        app_path.mkdir()
        mock_path.return_value = app_path

        mount_dir = tmp_path / "mount"
        mount_dir.mkdir()
        new_app = mount_dir / "WenZi.app"
        new_app.mkdir()
        (new_app / "Contents").mkdir()
        (new_app / "Contents" / "Info.plist").write_text("test")
        mock_mount.return_value = mount_dir

        on_ready = MagicMock()
        on_error = MagicMock()

        updater = AppUpdater(
            dmg_url="https://x.com/WenZi-2.0.dmg",
            version="2.0.0",
            on_ready=on_ready,
            on_error=on_error,
        )
        updater._run()

        staged = app_path.parent / ".WenZi-update.app"
        assert staged.exists()
        assert (staged / "Contents" / "Info.plist").read_text() == "test"

        on_ready.assert_called_once()
        on_error.assert_not_called()
        mock_verify.assert_called_once_with(staged)
        mock_unmount.assert_called_once_with(mount_dir)

    @patch("wenzi.updater.AppUpdater.is_writable", return_value=False)
    @patch("wenzi.updater.AppUpdater.get_app_bundle_path")
    def test_no_write_permission(self, mock_path, mock_writable, tmp_path):
        mock_path.return_value = tmp_path / "WenZi.app"

        on_error = MagicMock()
        updater = AppUpdater(
            dmg_url="https://x.com/a.dmg",
            version="1.0",
            on_error=on_error,
        )
        updater._run()

        on_error.assert_called_once()
        assert "write" in on_error.call_args[0][0].lower()

    @patch("wenzi.updater.AppUpdater._download_dmg")
    @patch("wenzi.updater.AppUpdater.is_writable", return_value=True)
    @patch("wenzi.updater.AppUpdater.get_app_bundle_path")
    def test_cancelled_after_download(
        self, mock_path, mock_writable, mock_download, tmp_path
    ):
        app_path = tmp_path / "WenZi.app"
        app_path.mkdir()
        mock_path.return_value = app_path

        on_ready = MagicMock()
        updater = AppUpdater(
            dmg_url="https://x.com/a.dmg",
            version="1.0",
            on_ready=on_ready,
        )

        def cancel_during_download(dest):
            updater.cancel()

        mock_download.side_effect = cancel_during_download
        updater._run()

        on_ready.assert_not_called()

    @patch("wenzi.updater.AppUpdater._mount_dmg")
    @patch("wenzi.updater.AppUpdater._download_dmg")
    @patch("wenzi.updater.AppUpdater.is_writable", return_value=True)
    @patch("wenzi.updater.AppUpdater.get_app_bundle_path")
    def test_mount_failure_calls_error(
        self, mock_path, mock_writable, mock_download, mock_mount, tmp_path
    ):
        app_path = tmp_path / "WenZi.app"
        app_path.mkdir()
        mock_path.return_value = app_path
        mock_mount.side_effect = UpdateError("Failed to mount DMG: bad image")

        on_error = MagicMock()
        updater = AppUpdater(
            dmg_url="https://x.com/a.dmg",
            version="1.0",
            on_error=on_error,
        )
        updater._run()

        on_error.assert_called_once()
        assert "mount" in on_error.call_args[0][0].lower()
