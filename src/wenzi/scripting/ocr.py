"""OCR text recognition using macOS Vision framework.

Provides a simple interface to extract text from images using
VNRecognizeTextRequest.  Runs OCR in a **subprocess** so that CoreML
model weights, Espresso graph objects, and Vision framework caches
(~84 MB combined) are freed when the worker exits instead of being
cached permanently in the main process.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

# VNRequestTextRecognitionLevelFast = 0
# Accurate (1) only supports 6 Latin languages; Fast covers all 30+
# including zh-Hans/zh-Hant, and is sufficient for clipboard OCR.
_RECOGNITION_LEVEL_FAST = 0

_DEFAULT_LANGUAGES = ["zh-Hans", "zh-Hant", "en-US"]
_OCR_WORKER_FLAG = "--ocr-worker"


def recognize_text(
    image_path: str,
    languages: list[str] | None = None,
) -> str:
    """Extract text from an image file using macOS Vision framework.

    Spawns a short-lived subprocess that loads Vision/CoreML, performs
    recognition, writes the result to stdout, and exits — releasing all
    framework-cached memory back to the OS.

    Args:
        image_path: Absolute path to the image file.
        languages: Recognition languages. Defaults to zh-Hans, zh-Hant, en-US.

    Returns:
        Recognized text with lines joined by newline, or empty string on
        failure or if no text is found.
    """
    if languages is None:
        languages = _DEFAULT_LANGUAGES

    try:
        cmd = _build_command(image_path, languages)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
        logger.debug(
            "OCR subprocess failed (rc=%d): %s",
            result.returncode,
            result.stderr[:200],
        )
        return ""
    except subprocess.TimeoutExpired:
        logger.debug("OCR subprocess timed out for %s", image_path)
        return ""
    except Exception:
        logger.debug("OCR failed for %s", image_path, exc_info=True)
        return ""


# ------------------------------------------------------------------
# Subprocess command builder
# ------------------------------------------------------------------


def _build_command(image_path: str, languages: list[str]) -> list[str]:
    """Return the argv for the OCR worker subprocess."""
    langs_json = json.dumps(languages)
    if getattr(sys, "frozen", False):
        # PyInstaller bundle — re-invoke the frozen executable with a
        # special flag so __main__.py routes to the OCR worker.
        return [sys.executable, _OCR_WORKER_FLAG, image_path, langs_json]
    # Development — run this module directly.
    return [sys.executable, "-m", "wenzi.scripting.ocr", image_path, langs_json]


# ------------------------------------------------------------------
# In-process implementation (executed inside the subprocess)
# ------------------------------------------------------------------


def _recognize_local(image_path: str, languages: list[str]) -> str:
    """Perform OCR in the current process.  Called by the subprocess entry point."""
    import objc

    with objc.autorelease_pool():
        from Foundation import NSURL
        from Quartz import VNImageRequestHandler, VNRecognizeTextRequest

        image_url = NSURL.fileURLWithPath_(image_path)
        handler = VNImageRequestHandler.alloc().initWithURL_options_(
            image_url, None,
        )

        request = VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(_RECOGNITION_LEVEL_FAST)
        request.setRecognitionLanguages_(languages)

        success = handler.performRequests_error_([request], None)
        if not success:
            return ""

        results = request.results()
        if not results:
            return ""

        lines = []
        for observation in results:
            candidates = observation.topCandidates_(1)
            if candidates:
                text = str(candidates[0].string())
                if text:
                    lines.append(text)

        return "\n".join(lines)


# ------------------------------------------------------------------
# Subprocess entry point
# ------------------------------------------------------------------


def _main() -> None:
    """Entry point when invoked as ``python -m wenzi.scripting.ocr`` or
    via ``--ocr-worker`` in a frozen build.

    argv layout: ... <image_path> <languages_json>
    """
    image_path = sys.argv[-2]
    languages = json.loads(sys.argv[-1])
    try:
        text = _recognize_local(image_path, languages)
        sys.stdout.write(text)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
