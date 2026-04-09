"""Model and provider management extracted from WenZiApp."""

from __future__ import annotations

import json as _json
import logging
import os
import re
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from wenzi import async_loop

if TYPE_CHECKING:
    from wenzi.app import WenZiApp

from wenzi.config import is_keychain_enabled, save_config
from wenzi.i18n import t
from wenzi.statusbar import send_notification
from wenzi.transcription.model_registry import (
    PRESET_BY_ID,
    ModelPreset,
    RemoteASRModel,
    clear_model_cache,
    get_model_cache_dir,
    is_backend_available,
    is_model_cached,
    resolve_preset_from_config,
)
from wenzi.ui_helpers import (
    activate_for_dialog,
    restore_accessory,
    run_multiline_window,
    topmost_alert,
)

logger = logging.getLogger(__name__)

# Approximate total download size for all FunASR models (~502 MB).
_FUNASR_APPROX_SIZE = 502 * 1024 * 1024


def _get_dir_size(path) -> int:
    """Calculate total size of all files in a directory."""
    from pathlib import Path

    target = Path(path)
    if not target.exists():
        return 0
    total = 0
    try:
        for f in target.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total


def parse_asr_provider_text(text: str):
    """Parse ASR provider config text.

    Returns (name, base_url, api_key, models) on success,
    or a string error message on failure.
    """
    lines = text.strip().splitlines()
    fields = {}
    in_models = False
    models = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("models:"):
            in_models = True
            inline = stripped[len("models:"):].strip()
            if inline:
                models.append(inline)
            continue
        if in_models:
            is_indented = line.startswith(" ") or line.startswith("\t")
            if not is_indented and ":" in stripped:
                in_models = False
            else:
                models.append(stripped)
                continue
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            fields[key.strip().lower()] = val.strip()

    name = fields.get("name", "").strip()
    base_url = fields.get("base_url", "").strip()
    api_key = fields.get("api_key", "").strip()

    errors = []
    if not name:
        errors.append("name is required")
    if not base_url:
        errors.append("base_url is required")
    if not api_key:
        errors.append("api_key is required")
    if not models:
        errors.append("at least one model is required")

    if errors:
        return "\n".join(errors)

    return name, base_url, api_key, models


def parse_provider_text(text: str):
    """Parse the LLM provider config text.

    Returns (name, base_url, api_key, models, extra_body) on success,
    or a string error message on failure.
    """
    lines = text.strip().splitlines()
    fields = {}
    in_models = False
    models = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("models:"):
            in_models = True
            inline = stripped[len("models:"):].strip()
            if inline:
                models.append(inline)
            continue
        if in_models:
            is_indented = line.startswith(" ") or line.startswith("\t")
            if not is_indented and ":" in stripped:
                in_models = False
            else:
                models.append(stripped)
                continue
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            fields[key.strip().lower()] = val.strip()

    name = fields.get("name", "").strip()
    base_url = fields.get("base_url", "").strip()
    api_key = fields.get("api_key", "").strip()
    extra_body_raw = fields.get("extra_body", "").strip()

    extra_body = {}
    if extra_body_raw:
        try:
            extra_body = _json.loads(extra_body_raw)
            if not isinstance(extra_body, dict):
                return "extra_body must be a JSON object"
        except _json.JSONDecodeError as e:
            return f"extra_body is not valid JSON: {e}"

    errors = []
    if not name:
        errors.append("name is required")
    if not base_url:
        errors.append("base_url is required")
    if not api_key:
        errors.append("api_key is required")
    if not models:
        errors.append("at least one model is required")

    if errors:
        return "\n".join(errors)

    return name, base_url, api_key, models, extra_body


_PROVIDER_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_provider_name(name: str) -> str | None:
    """Validate provider name format.

    Returns None if valid, or an error message string if invalid.
    Allowed characters: letters, digits, hyphens, underscores.
    """
    if not name or not name.strip():
        return "name is required"
    name = name.strip()
    if not _PROVIDER_NAME_RE.match(name):
        return "name may only contain letters, digits, hyphens, and underscores"
    return None


def migrate_asr_config(asr_cfg: dict[str, Any]) -> None:
    """Migrate old flat base_url/api_key to provider format."""
    base_url = asr_cfg.pop("base_url", None)
    api_key = asr_cfg.pop("api_key", None)

    if not base_url or not api_key:
        return

    providers = asr_cfg.setdefault("providers", {})
    if providers:
        return

    if "groq.com" in base_url:
        name = "groq"
        models = ["whisper-large-v3-turbo"]
    else:
        name = "migrated"
        model = asr_cfg.get("model") or "whisper-large-v3-turbo"
        models = [model]

    providers[name] = {
        "base_url": base_url,
        "api_key": api_key,
        "models": models,
    }

    if asr_cfg.get("backend") == "whisper-api":
        asr_cfg["default_provider"] = name
        asr_cfg["default_model"] = models[0]

    logger.info("Migrated ASR config: base_url/api_key → provider '%s'", name)


class ModelController:
    """Handles model selection, provider add/remove, and download monitoring."""

    _ADD_ASR_PROVIDER_TEMPLATE = """\
name: my-provider
base_url: https://api.groq.com/openai/v1
api_key: gsk-xxx
models:
  whisper-large-v3-turbo"""

    _ASR_PROVIDER_DRAFT_FILENAME = ".asr_provider_draft"

    def __init__(self, app: WenZiApp) -> None:
        self._app = app

    # ── ASR provider draft management ─────────────────────────────────

    def _get_asr_provider_draft_path(self) -> str:
        return os.path.join(self._app._config_dir, self._ASR_PROVIDER_DRAFT_FILENAME)

    def _load_asr_provider_draft(self) -> str:
        draft_path = self._get_asr_provider_draft_path()
        try:
            with open(draft_path, encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                return content
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug("Could not read ASR provider draft: %s", e)
        return self._ADD_ASR_PROVIDER_TEMPLATE

    def _save_asr_provider_draft(self, text: str) -> None:
        draft_path = self._get_asr_provider_draft_path()
        try:
            os.makedirs(os.path.dirname(draft_path), exist_ok=True)
            with open(draft_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            logger.debug("Could not save ASR provider draft: %s", e)

    def _remove_asr_provider_draft(self) -> None:
        draft_path = self._get_asr_provider_draft_path()
        try:
            os.remove(draft_path)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug("Could not remove ASR provider draft: %s", e)

    # ── Shared verify-and-save logic ────────────────────────────────

    def _do_verify_and_save(
        self,
        *,
        config_section: str,
        name: str,
        base_url: str,
        api_key: str,
        models: list,
        mode: str,
        existing_names: list,
        verify_fn: Callable[..., str | None],
        save_fn: Callable[[str, str, str, list], None],
    ) -> dict:
        """Shared verify-and-save logic for both STT and LLM providers.

        Returns {ok: True} or {ok: False, error: str}.

        Parameters:
            config_section: config key for vault lookup ("asr" or "ai_enhance")
            existing_names: current provider names (for duplicate check)
            verify_fn: callable(base_url, api_key, model) -> error str or None
            save_fn: callable(name, base_url, api_key, models) -> None;
                     must save config and update menus
        """
        app = self._app

        # Validate name format
        name_err = validate_provider_name(name)
        if name_err:
            return {"ok": False, "error": name_err}

        # Check duplicate in add mode
        if mode == "add" and name in existing_names:
            return {"ok": False, "error": f"Provider '{name}' already exists"}

        # In edit mode, resolve API key
        actual_api_key = api_key
        if mode == "edit" and not api_key:
            existing = (
                app._config.get(config_section, {})
                .get("providers", {})
                .get(name, {})
            )
            actual_api_key = existing.get("api_key", "")
            if not actual_api_key and is_keychain_enabled(app._config):
                from wenzi.vault import get_vault

                actual_api_key = (
                    get_vault().get(
                        f"{config_section}.providers.{name}.api_key"
                    )
                    or ""
                )
            if not actual_api_key:
                return {
                    "ok": False,
                    "error": "No existing API key found and none provided",
                }

        # Verify connection
        err = verify_fn(base_url, actual_api_key, models[0])
        if err:
            return {"ok": False, "error": err}

        # Save config and update menus
        save_fn(name, base_url, actual_api_key, models)

        return {"ok": True}

    def do_verify_and_save_stt_provider(
        self,
        name: str,
        base_url: str,
        api_key: str,
        models: list,
        mode: str,
    ) -> dict:
        """Verify and save an STT provider. Returns {ok: True} or {ok: False, error: str}.

        Called from a background thread by settings_controller.
        """
        app = self._app
        providers = app._config.get("asr", {}).get("providers", {})

        def _verify(base_url, api_key, model):
            from wenzi.transcription.whisper_api import WhisperAPITranscriber

            return WhisperAPITranscriber.verify_provider(base_url, api_key, model)

        def _save(name, base_url, actual_api_key, models):
            app._config.setdefault("asr", {})
            providers_cfg = app._config["asr"].setdefault("providers", {})
            providers_cfg[name] = {
                "base_url": base_url,
                "api_key": actual_api_key,
                "models": models,
            }
            save_config(app._config, app._config_path)
            app._menu_builder.build_model_menu()

        return self._do_verify_and_save(
            config_section="asr",
            name=name,
            base_url=base_url,
            api_key=api_key,
            models=models,
            mode=mode,
            existing_names=list(providers),
            verify_fn=_verify,
            save_fn=_save,
        )

    def do_verify_and_save_provider(
        self,
        name: str,
        base_url: str,
        api_key: str,
        models: list,
        extra_body: dict,
        mode: str,
    ) -> dict:
        """Verify and save a provider. Returns {ok: True} or {ok: False, error: str}.

        This is a synchronous method that runs the async verify internally.
        Called from a background thread by settings_controller.
        """
        app = self._app

        if not app._enhancer:
            return {"ok": False, "error": "LLM enhancer not initialized"}

        def _verify(base_url, api_key, model):
            return async_loop.submit(
                app._enhancer.verify_provider(
                    base_url,
                    api_key,
                    model,
                    extra_body=extra_body or None,
                )
            ).result(timeout=15)

        def _save(name, base_url, actual_api_key, models):
            # In edit mode, remove old provider first
            if mode == "edit":
                app._enhancer.remove_provider(name)

            success = app._enhancer.add_provider(
                name,
                base_url,
                actual_api_key,
                models,
                extra_body=extra_body or None,
            )
            if not success:
                raise RuntimeError("Failed to initialize provider client")

            app._config.setdefault("ai_enhance", {})
            providers_cfg = app._config["ai_enhance"].setdefault("providers", {})
            pcfg_save: dict[str, Any] = {
                "base_url": base_url,
                "api_key": actual_api_key,
                "models": models,
            }
            if extra_body:
                pcfg_save["extra_body"] = extra_body
            providers_cfg[name] = pcfg_save
            save_config(app._config, app._config_path)
            app._menu_builder.build_llm_model_menu()

        try:
            return self._do_verify_and_save(
                config_section="ai_enhance",
                name=name,
                base_url=base_url,
                api_key=api_key,
                models=models,
                mode=mode,
                existing_names=list(app._enhancer.provider_names),
                verify_fn=_verify,
                save_fn=_save,
            )
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}

    # ── Local model selection ─────────────────────────────────────────

    def on_model_select(self, sender) -> None:
        """Handle local model menu item click."""
        app = self._app
        preset_id = sender._preset_id

        if preset_id == app._current_preset_id and not app._current_remote_asr:
            return

        if app._busy:
            send_notification(
                t("app.name"),
                t("notification.model.cannot_switch"),
                t("notification.model.cannot_switch.subtitle"),
            )
            return

        preset = PRESET_BY_ID[preset_id]
        app._busy = True

        for item in app._model_menu_items.values():
            item.set_callback(None)
        for item in app._remote_asr_menu_items.values():
            item.set_callback(None)

        old_preset_id = app._current_preset_id
        old_transcriber = app._transcriber

        def _do_switch():
            stop_event = threading.Event()
            monitor_thread = None

            try:
                # For Apple Speech, verify Siri/Dictation is enabled first
                if preset.backend == "apple":
                    from wenzi.transcription.apple import (
                        check_siri_available,
                        prompt_enable_siri,
                    )

                    app._set_status("statusbar.status.checking")
                    ok, err = check_siri_available(
                        language=preset.language
                        or app._config.get("asr", {}).get("language", "zh"),
                        on_device=(preset.model == "on-device"),
                    )
                    if not ok:
                        logger.warning("Apple Speech preflight failed: %s", err)
                        prompt_enable_siri()
                        app._set_status("statusbar.status.ready")
                        return

                app._set_status("statusbar.status.unloading")
                old_transcriber.cleanup()

                cached = is_model_cached(preset)
                if not cached:
                    monitor_args = self._make_download_monitor_args(preset)
                    monitor_thread = threading.Thread(
                        target=self._monitor_download_progress,
                        args=(stop_event, monitor_args),
                        daemon=True,
                    )
                    monitor_thread.start()
                else:
                    app._set_status("statusbar.status.loading")

                new_transcriber = app._create_transcriber_for_preset(preset)
                new_transcriber.initialize()

                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)

                app._transcriber = new_transcriber
                app._current_preset_id = preset_id
                app._current_remote_asr = None
                app._menu_builder.update_model_checkmarks()

                app._config["asr"]["preset"] = preset_id
                app._config["asr"]["backend"] = preset.backend
                app._config["asr"]["model"] = preset.model
                app._config["asr"]["language"] = preset.language
                app._config["asr"]["default_provider"] = None
                app._config["asr"]["default_model"] = None
                save_config(app._config, app._config_path)

                app._set_status("statusbar.status.ready")
                logger.info("Switched to model: %s", preset.display_name)
                try:
                    send_notification(
                        t("app.name"),
                        t("notification.model.switched"),
                        t("notification.model.switched.subtitle", name=preset.display_name),
                    )
                except Exception:
                    logger.debug("Notification unavailable, skipping")

            except Exception as e:
                stop_event.set()
                if monitor_thread:
                    monitor_thread.join(timeout=2)

                logger.error("Model switch failed: %s", e)
                app._set_status("statusbar.status.error")

                can_clear = preset.backend not in ("apple", "whisper-api")
                if can_clear:
                    result = topmost_alert(
                        title=t("alert.model.switch_failed.title"),
                        message=t("alert.model.switch_failed.cache_message",
                                  name=preset.display_name, error=str(e)[:200]),
                        ok=t("alert.model.cache_retry"),
                        cancel=t("common.close"),
                    )
                    restore_accessory()
                    if result == 1:
                        self._clear_cache_and_retry_switch(
                            preset, old_preset_id
                        )
                        return
                else:
                    topmost_alert(
                        title=t("alert.model.switch_failed.title"),
                        message=t("alert.model.switch_failed.message",
                                  name=preset.display_name, error=str(e)[:200]),
                    )
                    restore_accessory()

                self._try_restore_previous_model(old_preset_id)

            finally:
                for pid, item in app._model_menu_items.items():
                    p = PRESET_BY_ID[pid]
                    if is_backend_available(p.backend):
                        item.set_callback(self.on_model_select)
                for item in app._remote_asr_menu_items.values():
                    item.set_callback(self.on_remote_asr_select)
                app._busy = False

        threading.Thread(target=_do_switch, daemon=True).start()

    def _make_download_monitor_args(self, preset: ModelPreset):
        """Pre-compute monitor paths on the calling thread.

        Must be called BEFORE starting the monitor thread so that modelscope
        imports happen before parallel download threads cause import deadlocks.

        Returns (expected_size, monitor_dir, temp_dir) or None.
        """
        expected_size = self._get_expected_model_size(preset)
        if not expected_size:
            return None

        cache_dir = get_model_cache_dir(preset)
        if preset.backend == "funasr":
            # FunASR loads ASR + VAD + Punc in parallel under the same
            # parent dir; monitor the parent to capture total progress.
            monitor_dir = cache_dir.parent
        else:
            monitor_dir = cache_dir
        # modelscope downloads to a ._____temp sibling before moving
        # to the final path; monitor both to track real progress.
        temp_dir = monitor_dir.parent / "._____temp" / monitor_dir.name
        return expected_size, monitor_dir, temp_dir

    def _monitor_download_progress(
        self, stop_event: threading.Event, monitor_args
    ) -> None:
        """Monitor download progress by checking cache directory size.

        ``monitor_args`` should come from ``_make_download_monitor_args``,
        called on the parent thread before this thread starts.
        """
        app = self._app
        if monitor_args is None:
            app._set_status("statusbar.status.downloading")
            stop_event.wait()
            return

        expected_size, monitor_dir, temp_dir = monitor_args

        while not stop_event.is_set():
            current_size = _get_dir_size(monitor_dir) + _get_dir_size(temp_dir)
            pct = min(int(current_size / expected_size * 100), 99)
            app._set_status(f"DL {pct}%")
            stop_event.wait(1.0)

    def _get_expected_model_size(self, preset: ModelPreset) -> int | None:
        """Get expected total download size for a preset."""
        if preset.backend == "funasr":
            return _FUNASR_APPROX_SIZE

        if preset.backend == "mlx-whisper" and preset.model:
            try:
                from huggingface_hub import model_info

                info = model_info(preset.model, files_metadata=True)
                total = sum(
                    s.size for s in (info.siblings or []) if s.size is not None
                )
                return total if total > 0 else None
            except Exception:
                logger.debug("Could not get model size for %s", preset.model)
                return None

        return None

    def _try_restore_previous_model(self, old_preset_id: str | None) -> None:
        """Attempt to restore the previous model after a failed switch."""
        app = self._app
        if not old_preset_id or old_preset_id not in PRESET_BY_ID:
            return

        old_preset = PRESET_BY_ID[old_preset_id]
        try:
            logger.info("Restoring previous model: %s", old_preset.display_name)
            app._set_status("statusbar.status.restoring")
            restored = app._create_transcriber_for_preset(old_preset)
            restored.initialize()
            app._transcriber = restored
            app._current_preset_id = old_preset_id
            app._menu_builder.update_model_checkmarks()
            app._set_status("statusbar.status.ready")
            logger.info("Previous model restored")
        except Exception as e2:
            logger.error("Failed to restore previous model: %s", e2)
            app._set_status("statusbar.status.error")

    def _clear_cache_and_retry_switch(
        self, preset: ModelPreset, old_preset_id
    ) -> None:
        """Clear model cache and retry the switch."""
        app = self._app
        stop_event = threading.Event()
        monitor_thread = None
        try:
            app._set_status("statusbar.status.clearing")
            clear_model_cache(preset)

            monitor_args = self._make_download_monitor_args(preset)
            monitor_thread = threading.Thread(
                target=self._monitor_download_progress,
                args=(stop_event, monitor_args),
                daemon=True,
            )
            monitor_thread.start()

            new_transcriber = app._create_transcriber_for_preset(preset)
            new_transcriber.initialize()

            stop_event.set()
            monitor_thread.join(timeout=2)

            app._transcriber = new_transcriber
            app._current_preset_id = preset.id
            app._current_remote_asr = None
            app._menu_builder.update_model_checkmarks()

            app._config["asr"]["preset"] = preset.id
            app._config["asr"]["backend"] = preset.backend
            app._config["asr"]["model"] = preset.model
            app._config["asr"]["language"] = preset.language
            app._config["asr"]["default_provider"] = None
            app._config["asr"]["default_model"] = None
            save_config(app._config, app._config_path)

            app._set_status("statusbar.status.ready")
            logger.info("Model switched after cache clear: %s", preset.display_name)
        except Exception as e2:
            stop_event.set()
            if monitor_thread:
                monitor_thread.join(timeout=2)
            logger.error("Retry after cache clear failed: %s", e2)
            app._set_status("statusbar.status.error")
            topmost_alert(
                title=t("alert.model.switch_failed.title"),
                message=t("alert.model.switch_failed.retry_message", error=str(e2)[:200]),
            )
            restore_accessory()
            self._try_restore_previous_model(old_preset_id)
        finally:
            for pid, item in app._model_menu_items.items():
                p = PRESET_BY_ID[pid]
                if is_backend_available(p.backend):
                    item.set_callback(self.on_model_select)
            for item in app._remote_asr_menu_items.values():
                item.set_callback(self.on_remote_asr_select)
            app._busy = False

    # ── Remote ASR model selection ────────────────────────────────────

    def on_remote_asr_select(self, sender) -> None:
        """Handle remote ASR model menu item click."""
        app = self._app
        rm: RemoteASRModel = sender._remote_asr
        key = (rm.provider, rm.model)

        if key == app._current_remote_asr:
            return

        if app._busy:
            send_notification(
                t("app.name"),
                t("notification.model.cannot_switch"),
                t("notification.model.cannot_switch.subtitle"),
            )
            return

        app._busy = True
        old_transcriber = app._transcriber

        def _do_switch():
            try:
                app._set_status("statusbar.status.switching")
                old_transcriber.cleanup()

                new_transcriber = app._create_transcriber_for_remote(
                    base_url=rm.base_url,
                    api_key=rm.api_key,
                    model=rm.model,
                )
                new_transcriber.initialize()

                app._transcriber = new_transcriber
                app._current_remote_asr = key
                app._current_preset_id = None
                app._menu_builder.update_model_checkmarks()

                app._config["asr"]["default_provider"] = rm.provider
                app._config["asr"]["default_model"] = rm.model
                save_config(app._config, app._config_path)

                app._set_status("statusbar.status.ready")
                logger.info("Switched to remote ASR: %s", rm.display_name)
                try:
                    send_notification(
                        t("app.name"),
                        t("notification.model.switched"),
                        t("notification.model.switched.subtitle", name=rm.display_name),
                    )
                except Exception:
                    logger.debug("Notification unavailable, skipping")

            except Exception as e:
                logger.error("Remote ASR switch failed: %s", e)
                app._set_status("statusbar.status.error")
                try:
                    send_notification(
                        t("app.name"),
                        t("notification.model.switch_failed"),
                        str(e)[:100],
                    )
                except Exception:
                    logger.debug("Notification unavailable, skipping")
            finally:
                app._busy = False

        threading.Thread(target=_do_switch, daemon=True).start()

    # ── LLM model selection ───────────────────────────────────────────

    def on_llm_model_select(self, sender) -> None:
        """Handle LLM model menu item click."""
        app = self._app
        pname = sender._llm_provider
        mname = sender._llm_model
        if not app._enhancer:
            return
        if pname == app._enhancer.provider_name and mname == app._enhancer.model_name:
            return

        app._enhancer.provider_name = pname
        app._enhancer.model_name = mname

        current_key = (pname, mname)
        for key, item in app._llm_model_menu_items.items():
            item.state = 1 if key == current_key else 0

        app._config.setdefault("ai_enhance", {})
        app._config["ai_enhance"]["default_provider"] = pname
        app._config["ai_enhance"]["default_model"] = mname
        save_config(app._config, app._config_path)
        logger.info("LLM model set to: %s / %s", pname, mname)

    # ── ASR provider add/remove ───────────────────────────────────────

    def on_asr_add_provider(self, _) -> None:
        """Add a new ASR provider via multi-step dialog."""
        def _run():
            try:
                self._do_add_asr_provider()
            except Exception as e:
                logger.error("Add ASR provider failed: %s", e, exc_info=True)
            finally:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(restore_accessory)

        threading.Thread(target=_run, daemon=True).start()

    def _do_add_asr_provider(self) -> None:
        """Internal implementation for adding an ASR provider."""
        app = self._app
        template = self._load_asr_provider_draft()
        while True:
            resp = run_multiline_window(
                title=t("alert.provider.add_asr.title"),
                message=t("alert.provider.add_asr.message"),
                default_text=template,
                ok=t("alert.provider.verify"),
                dimensions=(380, 140),
            )
            if resp is None:
                self._save_asr_provider_draft(template)
                return

            parsed = parse_asr_provider_text(resp.text)
            if isinstance(parsed, str):
                activate_for_dialog()
                topmost_alert(t("alert.provider.validation_error"), parsed)
                template = resp.text
                self._save_asr_provider_draft(resp.text)
                continue

            name, base_url, api_key, models = parsed

            providers = app._config.get("asr", {}).get("providers", {})
            if name in providers:
                activate_for_dialog()
                topmost_alert(t("alert.provider.error"), t("alert.provider.already_exists", name=name))
                template = resp.text
                self._save_asr_provider_draft(resp.text)
                continue

            activate_for_dialog()
            topmost_alert(
                t("alert.provider.verify_title"),
                t("alert.provider.verify_message", url=base_url, model=models[0]),
            )

            from wenzi.transcription.whisper_api import WhisperAPITranscriber

            err = WhisperAPITranscriber.verify_provider(base_url, api_key, models[0])

            if err:
                activate_for_dialog()
                result = topmost_alert(
                    title=t("alert.provider.verify_failed.title"),
                    message=t("alert.provider.verify_failed.message", error=err),
                    ok=t("common.edit"),
                    cancel=t("common.cancel"),
                )
                if result != 1:
                    self._save_asr_provider_draft(resp.text)
                    return
                template = resp.text
                self._save_asr_provider_draft(resp.text)
                continue

            activate_for_dialog()
            result = topmost_alert(
                title=t("alert.provider.verify_passed.title"),
                message=t("alert.provider.verify_passed.message",
                           name=name, url=base_url, models=", ".join(models)),
                ok=t("common.save"),
                cancel=t("common.cancel"),
            )
            if result != 1:
                self._save_asr_provider_draft(resp.text)
                return

            app._config.setdefault("asr", {})
            providers_cfg = app._config["asr"].setdefault("providers", {})
            providers_cfg[name] = {
                "base_url": base_url,
                "api_key": api_key,
                "models": models,
            }
            save_config(app._config, app._config_path)
            self._remove_asr_provider_draft()

            app._menu_builder.build_model_menu()

            send_notification(
                t("app.name"), t("notification.provider.asr_added"), f"{name} ({', '.join(models)})"
            )
            logger.info("Added ASR provider: %s", name)
            return

    def on_asr_remove_provider(self, sender) -> None:
        """Remove an ASR provider after confirmation."""
        app = self._app
        try:
            pname = sender._provider_name

            activate_for_dialog()
            result = topmost_alert(
                title=t("alert.provider.remove_asr.title"),
                message=t("alert.provider.remove_asr.message", name=pname),
                ok=t("common.remove"),
                cancel=t("common.cancel"),
            )
            if result != 1:
                return

            if app._current_remote_asr and app._current_remote_asr[0] == pname:
                app._transcriber.cleanup()
                asr_cfg = app._config["asr"]
                app._transcriber = app._create_local_transcriber(
                    asr_cfg, "funasr", app._load_hotwords()
                )
                app._current_remote_asr = None
                app._current_preset_id = resolve_preset_from_config(
                    asr_cfg.get("backend", "funasr"),
                    asr_cfg.get("model"),
                )
                app._config["asr"]["default_provider"] = None
                app._config["asr"]["default_model"] = None

            providers_cfg = app._config.get("asr", {}).get("providers", {})
            providers_cfg.pop(pname, None)

            # Clean up Keychain entries for this provider
            if is_keychain_enabled(app._config):
                from wenzi.vault import get_vault
                get_vault().delete_prefix(f"asr.providers.{pname}.")

            save_config(app._config, app._config_path)

            app._menu_builder.build_model_menu()
            app._menu_builder.update_model_checkmarks()

            send_notification(t("app.name"), t("notification.provider.asr_removed"), pname)
            logger.info("Removed ASR provider: %s", pname)
        except Exception as e:
            logger.error("Remove ASR provider failed: %s", e, exc_info=True)
        finally:
            restore_accessory()

    # ── LLM provider add/remove ───────────────────────────────────────

    def on_enhance_add_provider(self, _) -> None:
        """Open Settings panel to LLM tab for adding a provider."""
        app = self._app
        if hasattr(app, '_settings_controller'):
            app._settings_controller.on_open_settings(None)

    def on_enhance_remove_provider(self, sender) -> None:
        """Open Settings panel to LLM tab for provider management."""
        app = self._app
        if hasattr(app, '_settings_controller'):
            app._settings_controller.on_open_settings(None)
