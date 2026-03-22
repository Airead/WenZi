# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WenZi.app (闻字)"""

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

# Collect native extensions (.so, .dylib, .metallib) and data files
mlx_datas, mlx_binaries, mlx_hiddenimports = collect_all('mlx')
mlx_whisper_datas, mlx_whisper_binaries, mlx_whisper_hiddenimports = collect_all('mlx_whisper')
sherpa_datas, sherpa_binaries, sherpa_hiddenimports = collect_all('sherpa_onnx')
librosa_datas, librosa_binaries, librosa_hiddenimports = collect_all('librosa')

a = Analysis(
    ['src/wenzi/__main__.py'],
    pathex=['src'],
    binaries=mlx_binaries + mlx_whisper_binaries + sherpa_binaries + librosa_binaries,
    datas=mlx_datas + mlx_whisper_datas + sherpa_datas + librosa_datas + [
        (os.path.join(_spec_dir, 'src/wenzi/audio/sounds'), 'wenzi/audio/sounds'),
        (os.path.join(_spec_dir, 'src/wenzi/enhance/data'), 'wenzi/enhance/data'),
        (os.path.join(_spec_dir, 'src/wenzi/locales'), 'wenzi/locales'),
        (os.path.join(_spec_dir, 'src/wenzi/ui/vendor'), 'wenzi/ui/vendor'),
        (os.path.join(_spec_dir, 'src/wenzi/ui/templates'), 'wenzi/ui/templates'),
    ],
    hiddenimports=mlx_hiddenimports + mlx_whisper_hiddenimports + sherpa_hiddenimports + librosa_hiddenimports + [
        # wenzi core
        'wenzi',
        'wenzi._build_info',
        'wenzi.app',
        'wenzi.config',
        'wenzi.hotkey',
        'wenzi.input',
        'wenzi.statusbar',
        'wenzi.usage_stats',
        'wenzi.lru_cache',
        'wenzi.ui_helpers',
        'wenzi.input_source',
        'wenzi.keychain',
        # wenzi.audio
        'wenzi.audio',
        'wenzi.audio.recorder',
        'wenzi.audio.recording_indicator',
        'wenzi.audio.sound_manager',
        # wenzi.transcription
        'wenzi.transcription',
        'wenzi.transcription.base',
        'wenzi.transcription.funasr',
        'wenzi.transcription.mlx',
        'wenzi.transcription.apple',
        'wenzi.transcription.sherpa',
        'wenzi.transcription.whisper_api',
        'wenzi.transcription.model_registry',
        'wenzi.transcription.punctuation',
        # wenzi.enhance
        'wenzi.enhance',
        'wenzi.enhance.enhancer',
        'wenzi.enhance.vocabulary',
        'wenzi.enhance.vocabulary_builder',
        'wenzi.enhance.repetition',
        'wenzi.enhance.auto_vocab_builder',
        'wenzi.enhance.conversation_history',
        'wenzi.enhance.preview_history',
        'wenzi.enhance.mode_loader',
        'wenzi.enhance.text_diff',
        # wenzi.ui
        'wenzi.ui',
        'wenzi.ui.result_window_web',
        'wenzi.ui.settings_window',
        'wenzi.ui.log_viewer_window',
        'wenzi.ui.history_browser_window',
        'wenzi.ui.history_browser_window_web',
        'wenzi.ui.live_transcription_overlay',
        'wenzi.ui.streaming_overlay',
        'wenzi.ui.stats_panel',
        'wenzi.ui.hud',
        'wenzi.ui.translate_webview',
        'wenzi.ui.vocab_build_window',
        # wenzi.controllers
        'wenzi.controllers',
        'wenzi.controllers.recording_controller',
        'wenzi.controllers.model_controller',
        'wenzi.controllers.enhance_controller',
        'wenzi.controllers.enhance_mode_controller',
        'wenzi.controllers.config_controller',
        'wenzi.controllers.settings_controller',
        'wenzi.controllers.preview_controller',
        'wenzi.controllers.update_controller',
        'wenzi.controllers.menu_builder',
        'wenzi.updater',
        # wenzi.scripting
        'wenzi.scripting',
        'wenzi.scripting.engine',
        'wenzi.scripting.registry',
        'wenzi.scripting.clipboard_monitor',
        'wenzi.scripting.snippet_expander',
        # wenzi.scripting.api
        'wenzi.scripting.api',
        'wenzi.scripting.api.alert',
        'wenzi.scripting.api.app',
        'wenzi.scripting.api.chooser',
        'wenzi.scripting.api.eventtap',
        'wenzi.scripting.api.execute',
        'wenzi.scripting.api.hotkey',
        'wenzi.scripting.api.notify',
        'wenzi.scripting.api.pasteboard',
        'wenzi.scripting.api.snippets',
        'wenzi.scripting.api.store',
        'wenzi.scripting.api.timer',
        # wenzi.scripting.sources
        'wenzi.scripting.sources',
        'wenzi.scripting.sources._mdquery',
        'wenzi.scripting.sources.app_source',
        'wenzi.scripting.sources.bookmark_source',
        'wenzi.scripting.sources.calculator_source',
        'wenzi.scripting.sources.clipboard_source',
        'wenzi.scripting.sources.command_source',
        'wenzi.scripting.sources.file_source',
        'wenzi.scripting.sources.query_history',
        'wenzi.scripting.sources.snippet_source',
        'wenzi.scripting.sources.usage_tracker',
        # wenzi.scripting.ui
        'wenzi.scripting.ui',
        'wenzi.scripting.ui.chooser_html',
        'wenzi.scripting.ui.chooser_panel',
        'wenzi.scripting.ui.leader_alert',
        'wenzi.scripting.ui.quicklook_panel',
        'wenzi.scripting.ui.quick_edit_panel',
        'wenzi.scripting.ui.snippet_editor_panel',
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
        'modelscope.utils.file_utils',
        'modelscope.hub.snapshot_download',
        'openai',
        'simpleeval',
        'pint',
        'yaml',
        # PyObjC frameworks
        'ApplicationServices',
        'CoreFoundation',
        'Quartz',
        'AppKit',
        'Speech',
        'WebKit',
        'Foundation',
        'AVFoundation',
        'PyObjCTools',
        'PyObjCTools.AppHelper',
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
    name='WenZi',
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
    name='WenZi',
)

app = BUNDLE(
    coll,
    name='WenZi.app',
    icon=os.path.join(_spec_dir, 'resources', 'icon.icns'),
    bundle_identifier='io.github.airead.wenzi',
    codesign_identity=os.environ.get('CODESIGN_IDENTITY', ''),
    info_plist={
        'CFBundleName': 'WenZi',
        'CFBundleDisplayName': 'WenZi',
        'CFBundleVersion': _version,
        'CFBundleShortVersionString': _version,
        'LSUIElement': True,
        'NSMicrophoneUsageDescription': 'WenZi needs microphone access to record speech for transcription.',
        'NSAppleEventsUsageDescription': 'WenZi needs accessibility access to type transcribed text.',
        'NSSpeechRecognitionUsageDescription': 'WenZi needs speech recognition access for Apple Speech transcription.',
    },
)
