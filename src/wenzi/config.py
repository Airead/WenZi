"""Configuration for WenZi app."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from wenzi.vault import get_vault

logger = logging.getLogger(__name__)

# Pre-compiled regex for stripping trailing commas in JSONC
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

# Modifier key choices for restart/cancel key config.
# Each entry is (config_value, display_label). Used by both validate_config()
# and the settings UI dropdown to keep them in sync.
MODIFIER_KEY_CHOICES = [
    ("space", "Space"), ("cmd", "Command"), ("cmd_r", "Command (Right)"),
    ("ctrl", "Control"), ("ctrl_r", "Control (Right)"),
    ("alt", "Option"), ("alt_r", "Option (Right)"),
    ("shift", "Shift"), ("shift_r", "Shift (Right)"), ("esc", "Esc"),
]
_VALID_MODIFIER_KEYS = {k for k, _ in MODIFIER_KEY_CHOICES}


# XDG Base Directory paths
DEFAULT_CONFIG_DIR = os.path.join("~", ".config", "WenZi")
DEFAULT_DATA_DIR = os.path.join("~", ".local", "share", "WenZi")
DEFAULT_CACHE_DIR = os.path.join("~", ".cache", "WenZi")
DEFAULT_LOG_DIR = os.path.join("~", "Library", "Logs", "WenZi")

# Config files (user-editable, suitable for Git tracking)
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.json")
DEFAULT_ENHANCE_MODES_DIR = os.path.join(DEFAULT_CONFIG_DIR, "enhance_modes")
DEFAULT_SCRIPTS_DIR = os.path.join(DEFAULT_CONFIG_DIR, "scripts")
DEFAULT_PLUGINS_DIR = os.path.join(DEFAULT_CONFIG_DIR, "plugins")
DEFAULT_SNIPPETS_DIR = os.path.join(DEFAULT_CONFIG_DIR, "snippets")

# Plugin registry (override with WENZI_REGISTRY_URL env var for local testing)
BUILTIN_REGISTRY_URL = os.environ.get(
    "WENZI_REGISTRY_URL",
    "https://raw.githubusercontent.com/Airead/WenZi/refs/heads/main/plugins/registry.toml",
)

# Data files (user-generated, not recoverable if deleted)
DEFAULT_CLIPBOARD_HISTORY_PATH = os.path.join(DEFAULT_DATA_DIR, "clipboard_history.json")
DEFAULT_CLIPBOARD_IMAGES_DIR = os.path.join(DEFAULT_DATA_DIR, "clipboard_images")
DEFAULT_CHOOSER_USAGE_PATH = os.path.join(DEFAULT_DATA_DIR, "chooser_usage.json")
DEFAULT_CHOOSER_HISTORY_PATH = os.path.join(DEFAULT_DATA_DIR, "chooser_history.json")
DEFAULT_SCRIPT_DATA_PATH = os.path.join(DEFAULT_DATA_DIR, "script_data.json")
DEFAULT_SNIPPET_LAST_CATEGORY_PATH = os.path.join(DEFAULT_DATA_DIR, "snippet_last_category")
# Cache files (can be safely deleted and regenerated)
DEFAULT_ICON_CACHE_DIR = os.path.join(DEFAULT_CACHE_DIR, "icon_cache")

# Legacy paths for migration from VoiceText → WenZi
_LEGACY_CONFIG_DIR = os.path.join("~", ".config", "VoiceText")
_LEGACY_CACHE_DIR = os.path.join("~", ".cache", "voicetext")
_LEGACY_LOG_DIR = os.path.join("~", "Library", "Logs", "VoiceText")
_LEGACY_BUNDLE_ID = "com.voicetext.app"


BUNDLE_ID = "io.github.airead.wenzi"
_DEFAULTS_KEY = "config_dir"


def _read_user_defaults_config_dir() -> str | None:
    """Read config_dir from NSUserDefaults (macOS preferences)."""
    try:
        from Foundation import NSUserDefaults

        defaults = NSUserDefaults.alloc().initWithSuiteName_(BUNDLE_ID)
        value = defaults.stringForKey_(_DEFAULTS_KEY)
        if value:
            return str(value)
    except Exception:
        logger.debug("NSUserDefaults not available, skipping", exc_info=True)
    return None


def save_config_dir_preference(path: str) -> None:
    """Save a custom config_dir to NSUserDefaults."""
    from Foundation import NSUserDefaults

    defaults = NSUserDefaults.alloc().initWithSuiteName_(BUNDLE_ID)
    defaults.setObject_forKey_(path, _DEFAULTS_KEY)
    defaults.synchronize()
    logger.info("Saved config_dir preference: %s", path)


def reset_config_dir_preference() -> None:
    """Remove the custom config_dir from NSUserDefaults (revert to default)."""
    from Foundation import NSUserDefaults

    defaults = NSUserDefaults.alloc().initWithSuiteName_(BUNDLE_ID)
    defaults.removeObjectForKey_(_DEFAULTS_KEY)
    defaults.synchronize()
    logger.info("Reset config_dir preference to default")


def resolve_config_dir(config_dir: str | None = None) -> str:
    """Return the expanded absolute config directory path.

    Priority: explicit argument > NSUserDefaults > default path.
    """
    if config_dir:
        return os.path.expanduser(config_dir)

    from_defaults = _read_user_defaults_config_dir()
    if from_defaults:
        return os.path.expanduser(from_defaults)

    return os.path.expanduser(DEFAULT_CONFIG_DIR)


def resolve_data_dir() -> str:
    """Return the expanded absolute data directory path."""
    return os.path.expanduser(DEFAULT_DATA_DIR)


def resolve_cache_dir() -> str:
    """Return the expanded absolute cache directory path."""
    return os.path.expanduser(DEFAULT_CACHE_DIR)


def _migrate_dir(legacy: str, new: str, label: str) -> None:
    """Rename a legacy directory to the new path if applicable."""
    old = os.path.expanduser(legacy)
    dst = os.path.expanduser(new)
    if os.path.isdir(old) and not os.path.exists(dst):
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(old, dst)
            logger.info("Migrated %s: %s -> %s", label, old, dst)
        except OSError:
            logger.warning("Failed to migrate %s: %s -> %s", label, old, dst, exc_info=True)


def _migrate_file(src_dir: str, dst_dir: str, filename: str) -> None:
    """Move a single file from *src_dir* to *dst_dir* if it exists.

    Tries ``os.rename`` first (atomic on the same filesystem).  If that
    fails (e.g. cross-device), falls back to copy without deleting the
    source so the original remains as a safety net.
    """
    import shutil

    old = os.path.join(src_dir, filename)
    new = os.path.join(dst_dir, filename)
    if os.path.isfile(old) and not os.path.exists(new):
        try:
            os.makedirs(dst_dir, exist_ok=True)
            try:
                os.rename(old, new)
            except OSError:
                shutil.copy2(old, new)
            logger.info("Migrated file: %s -> %s", old, new)
        except OSError:
            logger.warning("Failed to migrate file: %s -> %s", old, new, exc_info=True)


def migrate_legacy_paths() -> None:
    """Migrate VoiceText config/cache/log directories to WenZi paths.

    Called once at startup before config loading.  Each directory is moved
    only when the old path exists and the new path does not.
    """
    _migrate_dir(_LEGACY_CONFIG_DIR, DEFAULT_CONFIG_DIR, "config")
    _migrate_dir(_LEGACY_CACHE_DIR, os.path.join("~", ".cache", "wenzi"), "cache")
    _migrate_dir(_LEGACY_LOG_DIR, os.path.join("~", "Library", "Logs", "WenZi"), "logs")

    # Update NSUserDefaults if config_dir still points to the legacy path.
    try:
        from Foundation import NSUserDefaults

        old_expanded = os.path.expanduser(_LEGACY_CONFIG_DIR)
        # Check both old and new bundle IDs for stored preference.
        for suite in (_LEGACY_BUNDLE_ID, BUNDLE_ID):
            defaults = NSUserDefaults.alloc().initWithSuiteName_(suite)
            value = defaults.stringForKey_(_DEFAULTS_KEY)
            if value and os.path.expanduser(str(value)) == old_expanded:
                new_expanded = os.path.expanduser(DEFAULT_CONFIG_DIR)
                save_config_dir_preference(new_expanded)
                # Clean up old suite key if it was from the legacy bundle ID.
                if suite == _LEGACY_BUNDLE_ID:
                    defaults.removeObjectForKey_(_DEFAULTS_KEY)
                    defaults.synchronize()
                logger.info("Updated config_dir preference: %s -> %s", value, new_expanded)
                break
    except Exception:
        logger.debug("NSUserDefaults migration skipped", exc_info=True)


def _migrate_dir_safe(src: str, dst: str, label: str) -> None:
    """Move a directory tree from *src* to *dst* if applicable.

    Tries ``os.rename`` first (atomic on the same filesystem).  If that
    fails (e.g. cross-device), falls back to ``shutil.copytree`` without
    deleting the source so the original remains as a safety net.
    """
    import shutil

    old = os.path.expanduser(src)
    new = os.path.expanduser(dst)
    if os.path.isdir(old) and not os.path.exists(new):
        try:
            os.makedirs(os.path.dirname(new), exist_ok=True)
            try:
                os.rename(old, new)
            except OSError:
                shutil.copytree(old, new)
            logger.info("Migrated %s: %s -> %s", label, old, new)
        except OSError:
            logger.warning("Failed to migrate %s: %s -> %s", label, old, new, exc_info=True)


def migrate_xdg_paths() -> None:
    """Copy data and cache files from ~/.config/WenZi/ to XDG directories.

    Called once at startup after ``migrate_legacy_paths()``.  Each file or
    directory is copied only when the old path exists and the new path does
    not.  Source files are kept as a safety fallback.
    """
    config = os.path.expanduser(DEFAULT_CONFIG_DIR)
    data = os.path.expanduser(DEFAULT_DATA_DIR)
    cache = os.path.expanduser(DEFAULT_CACHE_DIR)

    # Data files: ~/.config/WenZi/* → ~/.local/share/WenZi/*
    _migrate_file(config, data, "conversation_history.jsonl")
    _migrate_dir_safe(
        os.path.join(config, "conversation_history_archives"),
        os.path.join(data, "conversation_history_archives"),
        "history archives",
    )
    _migrate_file(config, data, "clipboard_history.db")
    _migrate_file(config, data, "clipboard_history.json")
    _migrate_dir_safe(
        os.path.join(config, "clipboard_images"),
        os.path.join(data, "clipboard_images"),
        "clipboard images",
    )
    _migrate_file(config, data, "usage_stats.json")
    _migrate_dir_safe(
        os.path.join(config, "usage_stats"),
        os.path.join(data, "usage_stats"),
        "usage stats",
    )
    _migrate_file(config, data, "chooser_usage.json")
    _migrate_file(config, data, "script_data.json")

    # Cache files: ~/.config/WenZi/* → ~/.cache/WenZi/*
    _migrate_dir_safe(
        os.path.join(config, "icon_cache"),
        os.path.join(cache, "icon_cache"),
        "icon cache",
    )
    _migrate_file(config, cache, "_chooser.html")


DEFAULT_CONFIG: dict[str, Any] = {
    "language": "auto",
    "hotkeys": {"fn": True},
    "audio": {
        "sample_rate": 16000,
        "block_ms": 20,
        "device": None,
        "max_session_bytes": 20971520,
        "silence_rms": 20,
        "max_recording_seconds": 120,
    },
    "asr": {
        "backend": "funasr",
        "use_vad": True,
        "use_punc": True,
        "language": "zh",
        "model": None,
        "preset": None,
        "temperature": 0.0,
        "default_provider": None,
        "default_model": None,
        "providers": {},
    },
    "output": {
        "method": "auto",
        "append_newline": False,
        "preview": True,
    },
    "ai_enhance": {
        "enabled": False,
        "mode": "proofread",
        "default_provider": "ollama",
        "default_model": "qwen2.5:7b",
        "providers": {
            "ollama": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "ollama",
                "models": ["qwen2.5:7b"],
            },
        },
        "thinking": False,
        "timeout": 10,
        "connection_timeout": 10,
        "max_retries": 2,
        "vocabulary": {
            "max_dynamic_hotwords": 10,
            "stats_include_app": False,
        },
        "conversation_history": {
            "enabled": False,
            "max_entries": 10,
        },
        "input_context": "basic",
    },
    "clipboard_enhance": {
        "hotkey": "ctrl+cmd+v",
    },
    "screenshot": {
        "enabled": False,
        "hotkey": "cmd+ctrl+a",
    },
    "feedback": {
        "sound_enabled": True,
        "sound_volume": 0.4,
        "visual_indicator": True,
        "show_device_name": False,
        "hide_status_icon": False,
        "restart_key": "cmd",
        "cancel_key": "space",
    },
    "ui": {
        "settings_last_tab": "general",
        "diff_panel_open": False,
    },
    "update_check": {
        "enabled": True,
        "interval_hours": 6,
    },
    "logging": {
        "level": "DEBUG",
    },
    "scripting": {
        "enabled": False,
        "script_dir": None,
        "chooser": {
            "enabled": False,
            "hotkey": "cmd+space",
            "clipboard_history": False,
            "clipboard_max_items": 50,
            "app_search": True,
            "file_search": True,
            "snippets": False,
            "bookmarks": True,
            "usage_learning": True,
            "prefixes": {
                "clipboard": "cb",
                "files": "f",
                "snippets": "sn",
                "bookmarks": "bm",
            },
            "source_hotkeys": {
                "clipboard": "",
                "files": "",
                "snippets": "",
                "bookmarks": "",
            },
            "new_snippet_hotkey": "",
            "clipboard_ocr": True,
            "switch_to_english": True,
            "pinyin": True,
            "recycle_mode": "preload_html",
            "universal_action_hotkey": "",
        },
    },
    "plugins": {
        "extra_registries": [],
    },
    "keychain": {
        "enabled": False,
    },
}

# FunASR model config (aligned with vocotype-cli)
MODEL_REVISION = os.environ.get("FUNASR_MODEL_REVISION", "v2.0.5")

MODELS = {
    "asr": os.environ.get(
        "FUNASR_ASR_MODEL",
        "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx",
    ),
    "vad": os.environ.get(
        "FUNASR_VAD_MODEL",
        "iic/speech_fsmn_vad_zh-cn-16k-common-onnx",
    ),
    "punc": os.environ.get(
        "FUNASR_PUNC_MODEL",
        "iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx",
    ),
}


def _strip_jsonc(text: str) -> str:
    """Strip JSONC features (// comments, /* */ block comments, trailing commas).

    Processes the text character-by-character to correctly handle comments
    inside strings (which must be preserved).
    """
    result: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False

    while i < n:
        ch = text[i]

        if in_string:
            result.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        # Not inside a string
        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            # Single-line comment: skip until end of line
            i += 2
            while i < n and text[i] != "\n":
                i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            # Block comment: skip until */
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2  # skip closing */
        else:
            result.append(ch)
            i += 1

    # Remove trailing commas before } or ]
    cleaned = "".join(result)
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)
    return cleaned


def _merge_dict(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    # Deep-copy list values from base so mutations don't affect DEFAULT_CONFIG
    for key, value in result.items():
        if isinstance(value, list):
            result[key] = list(value)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _ensure_default_config(config_path: str) -> None:
    """Create default config file if it does not exist."""
    config_dir = os.path.dirname(config_path)
    os.makedirs(config_dir, exist_ok=True)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(config_path, 0o600)

    logger.info("Created default config: %s", config_path)


_config_readonly: bool = False


def set_config_readonly(readonly: bool = True) -> None:
    """Set or clear the module-level readonly flag.

    When True, ``save_config()`` will silently skip writes to prevent
    overwriting the user's config file with defaults after a parse error.
    """
    global _config_readonly
    _config_readonly = readonly


def save_config(config: dict[str, Any], path: str | None = None) -> None:
    """Save configuration to a JSON file.

    If no path is given, uses ~/.config/WenZi/config.json.
    Writes are suppressed when the readonly flag is set.

    When Keychain integration is enabled, secret fields (api_key, base_url)
    are written to the macOS Keychain and replaced with sentinel values on
    disk.  The in-memory *config* dict is never modified.
    """
    if _config_readonly:
        logger.warning("Config save skipped: config is in readonly mode (parse error at startup)")
        return

    if not path:
        path = DEFAULT_CONFIG_PATH

    save_cfg = _scrub_secrets_for_disk(config)

    expanded = os.path.expanduser(path)
    config_dir = os.path.dirname(expanded)
    os.makedirs(config_dir, exist_ok=True)

    with open(expanded, "w", encoding="utf-8") as f:
        json.dump(save_cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(expanded, 0o600)

    logger.info("Config saved to %s", expanded)


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate config values and replace invalid ones with defaults.

    Logs a warning for each invalid value found.  Never raises — the app
    should always be able to start even with a broken config file.
    """
    rules = [
        # (dotted_path, expected_type, constraint, default_value)
        ("language", str, lambda v: v in {"auto", "en", "zh"}, DEFAULT_CONFIG["language"]),
        ("audio.sample_rate", int, lambda v: v > 0, DEFAULT_CONFIG["audio"]["sample_rate"]),
        ("audio.block_ms", int, lambda v: v > 0, DEFAULT_CONFIG["audio"]["block_ms"]),
        ("audio.max_session_bytes", int, lambda v: v > 0, DEFAULT_CONFIG["audio"]["max_session_bytes"]),
        ("audio.silence_rms", (int, float), lambda v: v >= 0, DEFAULT_CONFIG["audio"]["silence_rms"]),
        ("feedback.sound_enabled", bool, None, DEFAULT_CONFIG["feedback"]["sound_enabled"]),
        ("feedback.sound_volume", (int, float), lambda v: 0.0 <= v <= 1.0, DEFAULT_CONFIG["feedback"]["sound_volume"]),
        ("feedback.visual_indicator", bool, None, DEFAULT_CONFIG["feedback"]["visual_indicator"]),
        ("feedback.show_device_name", bool, None, DEFAULT_CONFIG["feedback"]["show_device_name"]),
        ("feedback.hide_status_icon", bool, None, DEFAULT_CONFIG["feedback"]["hide_status_icon"]),
        ("output.method", str, lambda v: v in {"auto", "paste", "clipboard"}, DEFAULT_CONFIG["output"]["method"]),
        ("output.append_newline", bool, None, DEFAULT_CONFIG["output"]["append_newline"]),
        ("asr.backend", str,
         lambda v: v in {"funasr", "mlx-whisper", "mlx_whisper", "whisper-api", "apple", "sherpa-onnx"},
         DEFAULT_CONFIG["asr"]["backend"]),
        ("asr.language", str, lambda v: len(v) > 0, DEFAULT_CONFIG["asr"]["language"]),
        ("logging.level", str, lambda v: v in {"DEBUG", "INFO", "WARNING", "ERROR"},
         DEFAULT_CONFIG["logging"]["level"]),
        ("ui.settings_last_tab", str, lambda v: v in {"general", "microphone", "stt", "ai", "launcher"},
         DEFAULT_CONFIG["ui"]["settings_last_tab"]),
        ("ai_enhance.timeout", (int, float), lambda v: v > 0, DEFAULT_CONFIG["ai_enhance"]["timeout"]),
        ("ai_enhance.connection_timeout", (int, float), lambda v: v > 0,
         DEFAULT_CONFIG["ai_enhance"]["connection_timeout"]),
        ("ai_enhance.max_retries", int, lambda v: v >= 0, DEFAULT_CONFIG["ai_enhance"]["max_retries"]),
        ("feedback.restart_key", str,
         lambda v: v in _VALID_MODIFIER_KEYS,
         DEFAULT_CONFIG["feedback"]["restart_key"]),
        ("feedback.cancel_key", str,
         lambda v: v in _VALID_MODIFIER_KEYS,
         DEFAULT_CONFIG["feedback"]["cancel_key"]),
        ("scripting.chooser.switch_to_english", bool, None,
         DEFAULT_CONFIG["scripting"]["chooser"]["switch_to_english"]),
        ("scripting.chooser.pinyin", bool, None,
         DEFAULT_CONFIG["scripting"]["chooser"]["pinyin"]),
        ("scripting.chooser.recycle_mode", str,
         lambda v: v in {"destroy", "prebuild", "preload_html", "keep_alive"},
         DEFAULT_CONFIG["scripting"]["chooser"]["recycle_mode"]),
        ("ai_enhance.vocabulary.max_dynamic_hotwords", int, lambda v: v > 0,
         DEFAULT_CONFIG["ai_enhance"]["vocabulary"]["max_dynamic_hotwords"]),
        ("ai_enhance.vocabulary.stats_include_app", bool, None,
         DEFAULT_CONFIG["ai_enhance"]["vocabulary"]["stats_include_app"]),
        ("ai_enhance.input_context", str, lambda v: v in {"off", "basic", "detailed"},
         DEFAULT_CONFIG["ai_enhance"]["input_context"]),
    ]

    for path, expected_type, constraint, default in rules:
        keys = path.split(".")
        # Navigate to the parent dict
        parent = config
        valid = True
        for key in keys[:-1]:
            if isinstance(parent, dict) and key in parent:
                parent = parent[key]
            else:
                valid = False
                break

        if not valid:
            continue

        leaf_key = keys[-1]
        if leaf_key not in parent:
            continue

        value = parent[leaf_key]

        # Type check
        if not isinstance(value, expected_type):
            logger.warning(
                "Config %s: invalid type %s (expected %s), using default: %r",
                path, type(value).__name__, expected_type, default,
            )
            parent[leaf_key] = default
            continue

        # Constraint check
        if constraint is not None:
            try:
                if not constraint(value):
                    logger.warning(
                        "Config %s: invalid value %r, using default: %r",
                        path, value, default,
                    )
                    parent[leaf_key] = default
            except Exception:
                logger.warning(
                    "Config %s: validation error for %r, using default: %r",
                    path, value, default,
                )
                parent[leaf_key] = default

    return config


KEYCHAIN_SENTINEL = "@keychain"
SECRET_SECTIONS = ["ai_enhance", "asr"]
SECRET_FIELDS = {"api_key", "base_url"}


def is_keychain_enabled(config: dict[str, Any]) -> bool:
    """Check if Keychain integration is enabled in config."""
    return config.get("keychain", {}).get("enabled", False)


def sync_secrets_to_keychain(config: dict[str, Any]) -> bool:
    """Sync sensitive provider fields with the encrypted vault.

    - Plaintext values → write to vault (value stays in memory as-is)
    - Sentinel values → read from vault, inject real value into memory

    The in-memory config always holds real values, never sentinels.
    Returns True if any plaintext was written to vault (caller should
    re-save to disk so the sentinels are persisted).
    """
    vault = get_vault()
    dirty = False
    for section in SECRET_SECTIONS:
        providers = config.get(section, {}).get("providers", {})
        for name, pcfg in providers.items():
            for field in SECRET_FIELDS:
                if field not in pcfg:
                    continue
                value = pcfg[field]
                account = f"{section}.providers.{name}.{field}"

                if value == KEYCHAIN_SENTINEL:
                    # Read real value from vault into memory
                    secret = vault.get(account)
                    if secret:
                        pcfg[field] = secret
                else:
                    # Write plaintext to vault; keep real value in memory
                    if vault.set(account, value):
                        dirty = True
                    else:
                        logger.warning(
                            "Failed to store %s in vault, keeping plaintext",
                            account,
                        )
    if dirty:
        vault.flush_sync()
    return dirty


def _scrub_secrets_for_disk(config: dict[str, Any]) -> dict[str, Any]:
    """Return a config copy with secret fields replaced by vault sentinels.

    When Keychain is disabled, returns the original dict unchanged (no copy).
    When enabled, deep-copies the config, writes each secret to the vault, and
    replaces the value with ``KEYCHAIN_SENTINEL`` on success.
    """
    if not is_keychain_enabled(config):
        return config

    import copy

    vault = get_vault()
    scrubbed = copy.deepcopy(config)
    any_written = False
    for section in SECRET_SECTIONS:
        providers = scrubbed.get(section, {}).get("providers", {})
        for name, pcfg in providers.items():
            for field in SECRET_FIELDS:
                if field not in pcfg or pcfg[field] == KEYCHAIN_SENTINEL:
                    continue
                account = f"{section}.providers.{name}.{field}"
                if vault.set(account, pcfg[field]):
                    pcfg[field] = KEYCHAIN_SENTINEL
                    any_written = True
    if any_written:
        vault.flush_sync()
    return scrubbed


# Backward-compatible alias — save_config now handles secrets internally.
save_config_with_secrets = save_config


class ConfigError:
    """Describes a configuration loading error."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def load_config(path: str | None = None) -> tuple[dict[str, Any], ConfigError | None]:
    """Load configuration from a JSON/JSONC file.

    Supports ``//`` and ``/* */`` comments as well as trailing commas.

    Returns:
        A tuple of (config_dict, error).  *error* is ``None`` on success or
        a :class:`ConfigError` describing the problem.  On error the returned
        config is the built-in default so the app can still start.
    """
    if not path:
        path = DEFAULT_CONFIG_PATH

    expanded = os.path.expanduser(path)

    if not os.path.exists(expanded):
        _ensure_default_config(expanded)
        return dict(DEFAULT_CONFIG), None

    try:
        with open(expanded, encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        msg = f"Cannot read config file: {exc}"
        logger.error(msg)
        return dict(DEFAULT_CONFIG), ConfigError(expanded, msg)

    try:
        cleaned = _strip_jsonc(raw)
        overrides = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Map error position back to the original file for a useful message
        lines = raw.splitlines()
        lineno = min(exc.lineno, len(lines)) if exc.lineno else 1
        context = lines[lineno - 1].rstrip() if lineno <= len(lines) else ""
        msg = (
            f"Syntax error on line {lineno}: {exc.msg}\n"
            f"  {context}\n"
            f"  {' ' * max(0, exc.colno - 1)}^"
        )
        logger.error("Config parse error in %s:\n%s", expanded, msg)
        return dict(DEFAULT_CONFIG), ConfigError(expanded, msg)

    if not isinstance(overrides, dict):
        msg = f"Config file must be a JSON object, got {type(overrides).__name__}"
        logger.error(msg)
        return dict(DEFAULT_CONFIG), ConfigError(expanded, msg)

    config = _merge_dict(DEFAULT_CONFIG, overrides)

    # Migrate legacy "hotkey" (string) → "hotkeys" (dict)
    if "hotkey" in config:
        old = config.pop("hotkey")
        if isinstance(old, str) and "hotkeys" not in overrides:
            config["hotkeys"] = {old: True}
            save_config(config, path)
            logger.info("Migrated hotkey %r → hotkeys dict", old)

    validate_config(config)

    # Sync secrets with macOS Keychain (only when enabled via config file)
    if is_keychain_enabled(config):
        if sync_secrets_to_keychain(config):
            save_config(config, path)

    return config, None
