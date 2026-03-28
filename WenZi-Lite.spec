# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WenZi-Lite.app (闻字 Lite — Apple Speech + Remote API only)"""

import os
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

# Read version from pyproject.toml (single source of truth)
_spec_dir = os.path.dirname(os.path.abspath(SPEC))
with open(os.path.join(_spec_dir, 'pyproject.toml'), 'rb') as _f:
    _pyproject = tomllib.load(_f)
_version = _pyproject['project']['version']

block_cipher = None

from PyInstaller.utils.hooks import collect_all
certifi_datas, certifi_binaries, certifi_hiddenimports = collect_all('certifi')

a = Analysis(
    ['src/wenzi/__main__.py'],
    pathex=['src'],
    binaries=certifi_binaries,
    datas=certifi_datas + [
        (os.path.join(_spec_dir, 'src/wenzi/audio/sounds'), 'wenzi/audio/sounds'),
        (os.path.join(_spec_dir, 'src/wenzi/enhance/data'), 'wenzi/enhance/data'),
        (os.path.join(_spec_dir, 'src/wenzi/locales'), 'wenzi/locales'),
        (os.path.join(_spec_dir, 'src/wenzi/ui/vendor'), 'wenzi/ui/vendor'),
        (os.path.join(_spec_dir, 'src/wenzi/ui/templates'), 'wenzi/ui/templates'),
    ],
    hiddenimports=certifi_hiddenimports + [
        # wenzi core
        'wenzi',
        'wenzi._build_info',
        'wenzi.app',
        'wenzi.async_loop',
        'wenzi.config',
        'wenzi.hotkey',
        'wenzi.input',
        'wenzi.statusbar',
        'wenzi.usage_stats',
        'wenzi.lru_cache',
        'wenzi.ui_helpers',
        'wenzi.input_source',
        'wenzi.input_context',
        'wenzi.keychain',
        'wenzi.vault',
        'wenzi.i18n',
        'wenzi.locales',
        # wenzi.audio
        'wenzi.audio',
        'wenzi.audio.recorder',
        'wenzi.audio.recording_indicator',
        'wenzi.audio.sound_manager',
        # wenzi.transcription (Lite: apple + whisper_api only)
        'wenzi.transcription',
        'wenzi.transcription.base',
        'wenzi.transcription.apple',
        'wenzi.transcription.whisper_api',
        'wenzi.transcription.model_registry',
        # wenzi.enhance
        'wenzi.enhance',
        'wenzi.enhance.enhancer',
        'wenzi.enhance.vocabulary',
        'wenzi.enhance.repetition',
        'wenzi.enhance.conversation_history',
        'wenzi.enhance.preview_history',
        'wenzi.enhance.mode_loader',
        'wenzi.enhance.text_diff',
        'wenzi.enhance.pool_monitor',
        # wenzi.ui
        'wenzi.ui',
        'wenzi.ui.result_window_web',
        'wenzi.ui.settings_window_web',
        'wenzi.ui.templates',
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
        'wenzi.controllers.recording_flow',
        'wenzi.updater',
        # wenzi.scripting
        'wenzi.scripting',
        'wenzi.scripting.engine',
        'wenzi.scripting.registry',
        'wenzi.scripting.clipboard_monitor',
        'wenzi.scripting.snippet_expander',
        'wenzi.scripting.ocr',
        'wenzi.scripting.plugin_installer',
        'wenzi.scripting.plugin_meta',
        'wenzi.scripting.plugin_registry',
        # wenzi.scripting.api
        'wenzi.scripting.api',
        'wenzi.scripting.api._async_util',
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
        'wenzi.scripting.api.ui',
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
        'wenzi.scripting.sources.system_settings_source',
        # wenzi.scripting.ui
        'wenzi.scripting.ui',
        'wenzi.scripting.ui.chooser_panel',
        'wenzi.scripting.ui.leader_alert',
        'wenzi.scripting.ui.quicklook_panel',
        'wenzi.scripting.ui.quick_edit_panel',
        'wenzi.scripting.ui.snippet_editor_panel',
        'wenzi.scripting.ui.webview_panel',
        # third-party (Lite only — no local ASR packages)
        'sounddevice',
        'soundfile',
        'numpy',
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._darwin',
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
    excludes=[
        'mlx',
        'mlx_whisper',
        'sherpa_onnx',
        'sherpa_onnx_core',
        'librosa',
        'funasr_onnx',
        'jieba',
        'onnxruntime',
        'sentencepiece',
        'tiktoken',
        'huggingface_hub',
        'modelscope',
    ],
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
    name='WenZi-Lite',
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
    name='WenZi-Lite',
)

app = BUNDLE(
    coll,
    name='WenZi-Lite.app',
    icon=os.path.join(_spec_dir, 'resources', 'icon.icns'),
    bundle_identifier='io.github.airead.wenzi',
    codesign_identity=os.environ.get('CODESIGN_IDENTITY', ''),
    info_plist={
        'CFBundleName': 'WenZi Lite',
        'CFBundleDisplayName': 'WenZi Lite',
        'CFBundleVersion': _version,
        'CFBundleShortVersionString': _version,
        'LSUIElement': True,
        'NSMicrophoneUsageDescription': 'WenZi needs microphone access to record speech for transcription.',
        'NSAppleEventsUsageDescription': 'WenZi needs accessibility access to type transcribed text.',
        'NSSpeechRecognitionUsageDescription': 'WenZi needs speech recognition access for Apple Speech transcription.',
    },
)
