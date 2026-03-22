"""Tests for the HTML template loader."""

import pytest

import wenzi.ui.templates as templates_mod
from wenzi.ui.templates import load_template


def test_single_placeholder_replacement(monkeypatch, tmp_path):
    """load_template replaces a single __KEY__ placeholder."""
    template_file = tmp_path / "single.html"
    template_file.write_text("<p>__GREETING__</p>", encoding="utf-8")
    monkeypatch.setattr(templates_mod, "_TEMPLATES_DIR", tmp_path)

    result = load_template("single.html", GREETING="Hello, World!")

    assert result == "<p>Hello, World!</p>"


def test_multiple_placeholder_replacement(monkeypatch, tmp_path):
    """load_template replaces multiple __KEY__ placeholders."""
    template_file = tmp_path / "multi.html"
    template_file.write_text(
        "<h1>__TITLE__</h1><p>__BODY__</p>", encoding="utf-8"
    )
    monkeypatch.setattr(templates_mod, "_TEMPLATES_DIR", tmp_path)

    result = load_template("multi.html", TITLE="My Title", BODY="My Body")

    assert result == "<h1>My Title</h1><p>My Body</p>"


def test_no_replacements_returns_raw_content(monkeypatch, tmp_path):
    """load_template with no replacements returns the raw template content."""
    raw_html = "<html><body>No placeholders here.</body></html>"
    template_file = tmp_path / "raw.html"
    template_file.write_text(raw_html, encoding="utf-8")
    monkeypatch.setattr(templates_mod, "_TEMPLATES_DIR", tmp_path)

    result = load_template("raw.html")

    assert result == raw_html


def test_missing_file_raises_file_not_found(monkeypatch, tmp_path):
    """load_template raises FileNotFoundError when the template does not exist."""
    monkeypatch.setattr(templates_mod, "_TEMPLATES_DIR", tmp_path)

    with pytest.raises(FileNotFoundError):
        load_template("nonexistent.html")


def test_integration_settings_window_web_html():
    """Integration: load the real settings_window_web.html template."""
    result = load_template("settings_window_web.html", CONFIG='{"test": true}')
    assert "<!DOCTYPE html>" in result
    assert '{"test": true}' in result
    assert "__CONFIG__" not in result
