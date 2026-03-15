# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for VoiceText.app"""

import os
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

# Read version from pyproject.toml (single source of truth)
_spec_dir = os.path.dirname(os.path.abspath(SPEC))
with open(os.path.join(_spec_dir, 'pyproject.toml'), 'rb') as _f:
    _pyproject = tomllib.load(_f)
_version = _pyproject['project']['version']

block_cipher = None

# Collect mlx native extensions (.so, .dylib, .metallib) and data files
mlx_datas, mlx_binaries, mlx_hiddenimports = collect_all('mlx')
mlx_whisper_datas, mlx_whisper_binaries, mlx_whisper_hiddenimports = collect_all('mlx_whisper')
fastembed_datas, fastembed_binaries, fastembed_hiddenimports = collect_all('fastembed')

a = Analysis(
    ['src/voicetext/__main__.py'],
    pathex=['src'],
    binaries=mlx_binaries + mlx_whisper_binaries + fastembed_binaries,
    datas=mlx_datas + mlx_whisper_datas + fastembed_datas,
    hiddenimports=mlx_hiddenimports + mlx_whisper_hiddenimports + fastembed_hiddenimports + [
        # voicetext core
        'voicetext',
        'voicetext.app',
        'voicetext.config',
        'voicetext.hotkey',
        'voicetext.input',
        'voicetext.statusbar',
        'voicetext.usage_stats',
        'voicetext.lru_cache',
        'voicetext.ui_helpers',
        # voicetext.audio
        'voicetext.audio',
        'voicetext.audio.recorder',
        'voicetext.audio.recording_indicator',
        'voicetext.audio.sound_manager',
        # voicetext.transcription
        'voicetext.transcription',
        'voicetext.transcription.base',
        'voicetext.transcription.funasr',
        'voicetext.transcription.mlx',
        'voicetext.transcription.apple',
        'voicetext.transcription.sherpa',
        'voicetext.transcription.whisper_api',
        'voicetext.transcription.model_registry',
        'voicetext.transcription.punctuation',
        # voicetext.enhance
        'voicetext.enhance',
        'voicetext.enhance.enhancer',
        'voicetext.enhance.vocabulary',
        'voicetext.enhance.vocabulary_builder',
        'voicetext.enhance.auto_vocab_builder',
        'voicetext.enhance.conversation_history',
        'voicetext.enhance.preview_history',
        'voicetext.enhance.mode_loader',
        # voicetext.ui
        'voicetext.ui',
        'voicetext.ui.result_window',
        'voicetext.ui.result_window_web',
        'voicetext.ui.settings_window',
        'voicetext.ui.log_viewer_window',
        'voicetext.ui.history_browser_window',
        'voicetext.ui.history_browser_window_web',
        'voicetext.ui.live_transcription_overlay',
        'voicetext.ui.streaming_overlay',
        'voicetext.ui.stats_panel',
        'voicetext.ui.translate_webview',
        'voicetext.ui.vocab_build_window',
        # voicetext.controllers
        'voicetext.controllers',
        'voicetext.controllers.recording_controller',
        'voicetext.controllers.model_controller',
        'voicetext.controllers.enhance_controller',
        'voicetext.controllers.enhance_mode_controller',
        'voicetext.controllers.config_controller',
        'voicetext.controllers.settings_controller',
        'voicetext.controllers.preview_controller',
        'voicetext.controllers.menu_builder',
        # voicetext.scripting
        'voicetext.scripting',
        'voicetext.scripting.engine',
        'voicetext.scripting.registry',
        'voicetext.scripting.api',
        'voicetext.scripting.ui',
        # third-party
        'sounddevice',
        'soundfile',
        'numpy',
        'librosa',
        'funasr_onnx',
        'funasr_onnx.paraformer_bin',
        'funasr_onnx.vad_bin',
        'funasr_onnx.punc_bin',
        'funasr_onnx.utils.utils',
        'funasr_onnx.utils.frontend',
        'jieba',
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._darwin',
        'onnxruntime',
        'sentencepiece',
        'tiktoken',
        'huggingface_hub',
        'sherpa_onnx',
        # PyObjC frameworks
        'ApplicationServices',
        'CoreFoundation',
        'Quartz',
        'AppKit',
        'Speech',
        'WebKit',
        'AVFoundation',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoiceText',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=None,
    codesign_identity=os.environ.get('CODESIGN_IDENTITY', ''),
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='VoiceText',
)

app = BUNDLE(
    coll,
    name='VoiceText.app',
    icon=os.path.join(_spec_dir, 'resources', 'icon.icns'),
    bundle_identifier='com.voicetext.app',
    codesign_identity=os.environ.get('CODESIGN_IDENTITY', ''),
    info_plist={
        'CFBundleName': 'VoiceText',
        'CFBundleDisplayName': 'VoiceText',
        'CFBundleVersion': _version,
        'CFBundleShortVersionString': _version,
        'LSUIElement': True,
        'NSMicrophoneUsageDescription': 'VoiceText needs microphone access to record speech for transcription.',
        'NSAppleEventsUsageDescription': 'VoiceText needs accessibility access to type transcribed text.',
    },
)
