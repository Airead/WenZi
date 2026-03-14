"""Tests for SoundManager."""

import os
from unittest.mock import MagicMock, patch

from voicetext.audio.sound_manager import SoundManager, ensure_start_sound


class TestSoundManagerInit:
    def test_default_enabled(self):
        sm = SoundManager()
        assert sm.enabled is True

    def test_custom_disabled(self):
        sm = SoundManager(enabled=False)
        assert sm.enabled is False


class TestSoundManagerEnabled:
    def test_toggle_enabled(self):
        sm = SoundManager(enabled=True)
        sm.enabled = False
        assert sm.enabled is False
        sm.enabled = True
        assert sm.enabled is True


class TestSoundManagerPlay:
    @patch("voicetext.audio.sound_manager.SoundManager._play_on_main_thread")
    def test_play_when_disabled_does_nothing(self, mock_play):
        sm = SoundManager(enabled=False)
        sm.play("start")
        mock_play.assert_not_called()

    @patch("voicetext.audio.sound_manager.SoundManager._play_on_main_thread")
    def test_play_stop_event_does_nothing(self, mock_play):
        sm = SoundManager(enabled=True)
        sm.play("stop")
        mock_play.assert_not_called()

    @patch("voicetext.audio.sound_manager.SoundManager._play_on_main_thread")
    def test_play_unknown_event_does_nothing(self, mock_play):
        sm = SoundManager(enabled=True)
        sm.play("unknown_event")
        mock_play.assert_not_called()

    def test_play_on_main_thread_file_not_found(self):
        sm = SoundManager(enabled=True)
        # Should not raise
        sm._play_on_main_thread("/nonexistent/path.wav")

    def test_play_on_main_thread_with_mock_nssound(self, tmp_path):
        dummy = tmp_path / "test.wav"
        dummy.write_bytes(b"fake")

        mock_sound_instance = MagicMock()
        mock_nssound = MagicMock()
        mock_nssound.alloc.return_value.initWithContentsOfFile_byReference_.return_value = (
            mock_sound_instance
        )

        mock_appkit = MagicMock()
        mock_appkit.NSSound = mock_nssound

        sm = SoundManager(enabled=True, volume=0.5)
        with patch.dict("sys.modules", {"AppKit": mock_appkit}):
            sm._play_on_main_thread(str(dummy))

        mock_nssound.alloc.return_value.initWithContentsOfFile_byReference_.assert_called_with(
            str(dummy), True
        )
        mock_sound_instance.setVolume_.assert_called_with(0.5)
        mock_sound_instance.play.assert_called_once()


class TestEnsureStartSound:
    def test_generates_wav_in_sounds_dir(self, tmp_path, monkeypatch):
        sounds_dir = str(tmp_path / "sounds")
        monkeypatch.setattr("voicetext.audio.sound_manager.SOUNDS_DIR", sounds_dir)
        path = ensure_start_sound()
        assert os.path.exists(path)
        assert path.endswith("recording_start.wav")
        assert sounds_dir.replace("~", os.path.expanduser("~")) in path

    def test_does_not_regenerate_if_exists(self, tmp_path, monkeypatch):
        sounds_dir = str(tmp_path / "sounds")
        monkeypatch.setattr("voicetext.audio.sound_manager.SOUNDS_DIR", sounds_dir)
        path1 = ensure_start_sound()
        mtime1 = os.path.getmtime(path1)
        path2 = ensure_start_sound()
        mtime2 = os.path.getmtime(path2)
        assert path1 == path2
        assert mtime1 == mtime2

    def test_user_can_replace_sound(self, tmp_path, monkeypatch):
        sounds_dir = str(tmp_path / "sounds")
        monkeypatch.setattr("voicetext.audio.sound_manager.SOUNDS_DIR", sounds_dir)
        # Generate default first
        path = ensure_start_sound()
        # User replaces with custom file
        with open(path, "wb") as f:
            f.write(b"custom sound data")
        # Should return the same path without overwriting
        path2 = ensure_start_sound()
        assert path == path2
        with open(path2, "rb") as f:
            assert f.read() == b"custom sound data"

    def test_fallback_when_blow_missing(self, tmp_path, monkeypatch):
        sounds_dir = str(tmp_path / "sounds")
        monkeypatch.setattr("voicetext.audio.sound_manager.SOUNDS_DIR", sounds_dir)

        original_exists = os.path.exists

        def fake_exists(p):
            if "Blow.aiff" in str(p):
                return False
            return original_exists(p)

        monkeypatch.setattr("os.path.exists", fake_exists)
        path = ensure_start_sound()
        assert os.path.exists(path)
