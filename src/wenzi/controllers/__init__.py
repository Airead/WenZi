"""Controllers subpackage — business logic controllers for the app."""

import logging

_logger = logging.getLogger(__name__)


def fire_scripting_event(app, event_name: str, **kwargs) -> None:
    """Fire a scripting event if the script engine is available."""
    engine = getattr(app, "_script_engine", None)
    if engine is None:
        return
    try:
        engine.wz._registry.fire_event(event_name, **kwargs)
    except Exception:
        _logger.debug("Failed to fire scripting event %s", event_name)


from .config_controller import ConfigController  # noqa: E402
from .enhance_controller import EnhanceCacheEntry, EnhanceController  # noqa: E402
from .enhance_mode_controller import EnhanceModeController  # noqa: E402
from .menu_builder import MenuBuilder  # noqa: E402
from .model_controller import ModelController, migrate_asr_config  # noqa: E402
from .preview_controller import PreviewController  # noqa: E402
from .recording_controller import RecordingController  # noqa: E402
from .recording_flow import Action, RecordingFlow  # noqa: E402
from .settings_controller import SettingsController  # noqa: E402
from .update_controller import UpdateController  # noqa: E402
from .vocab_controller import VocabController  # noqa: E402

__all__ = [
    "ConfigController",
    "EnhanceCacheEntry",
    "EnhanceController",
    "EnhanceModeController",
    "MenuBuilder",
    "ModelController",
    "PreviewController",
    "Action",
    "RecordingController",
    "RecordingFlow",
    "SettingsController",
    "UpdateController",
    "VocabController",
    "migrate_asr_config",
]
