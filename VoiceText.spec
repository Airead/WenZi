# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for VoiceText.app"""

import os
import sys

block_cipher = None

a = Analysis(
    ['src/voicetext/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'voicetext',
        'voicetext.config',
        'voicetext.hotkey',
        'voicetext.recorder',
        'voicetext.transcriber',
        'voicetext.input',
        'rumps',
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
        'ApplicationServices',
        'CoreFoundation',
        'Quartz',
        'AppKit',
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
    codesign_identity=None,
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
    icon=None,
    bundle_identifier='com.voicetext.app',
    codesign_identity=os.environ.get('CODESIGN_IDENTITY', '-'),
    info_plist={
        'CFBundleName': 'VoiceText',
        'CFBundleDisplayName': 'VoiceText',
        'CFBundleVersion': '0.1.0',
        'CFBundleShortVersionString': '0.1.0',
        'LSUIElement': True,
        'NSMicrophoneUsageDescription': 'VoiceText needs microphone access to record speech for transcription.',
        'NSAppleEventsUsageDescription': 'VoiceText needs accessibility access to type transcribed text.',
    },
)
