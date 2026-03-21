"""Tests for cc_sessions.preview module."""

from __future__ import annotations

from cc_sessions.preview import build_preview_html


class TestBuildPreviewHtml:
    """Test HTML preview builder."""

    def test_contains_metadata(self):
        session = {
            "project": "VoiceText",
            "git_branch": "main",
            "version": "2.1.81",
            "message_count": 42,
            "created": "2026-03-21T10:00:00Z",
            "modified": "2026-03-21T12:00:00Z",
            "custom_title": "Config Refactor",
            "summary": "Refactored the config module",
            "first_prompt": "refactor the config",
        }
        detail = {
            "turns": [],
            "total_input_tokens": 5000,
            "total_output_tokens": 2000,
        }
        html = build_preview_html(session, detail)
        assert "VoiceText" in html
        assert "main" in html
        assert "2.1.81" in html
        assert "42" in html
        assert "Config Refactor" in html
        assert "5,000" in html or "5000" in html

    def test_contains_conversation_turns(self):
        session = {
            "project": "Test",
            "git_branch": "",
            "version": "",
            "message_count": 2,
            "created": "2026-01-01T00:00:00Z",
            "modified": "2026-01-01T00:01:00Z",
            "custom_title": "",
            "summary": "",
            "first_prompt": "Hello",
        }
        detail = {
            "turns": [
                {"role": "user", "text": "Hello world"},
                {"role": "assistant", "text": "Hi there!"},
            ],
            "total_input_tokens": 100,
            "total_output_tokens": 50,
        }
        html = build_preview_html(session, detail)
        assert "Hello world" in html
        assert "Hi there!" in html

    def test_duration_calculation(self):
        session = {
            "project": "Test",
            "git_branch": "",
            "version": "",
            "message_count": 0,
            "created": "2026-01-01T10:00:00Z",
            "modified": "2026-01-01T12:30:00Z",
            "custom_title": "",
            "summary": "",
            "first_prompt": "",
        }
        detail = {"turns": [], "total_input_tokens": 0, "total_output_tokens": 0}
        html = build_preview_html(session, detail)
        assert "2h 30m" in html

    def test_no_custom_title_uses_summary(self):
        session = {
            "project": "Test",
            "git_branch": "",
            "version": "",
            "message_count": 0,
            "created": "",
            "modified": "",
            "custom_title": "",
            "summary": "A good summary",
            "first_prompt": "original prompt",
        }
        detail = {"turns": [], "total_input_tokens": 0, "total_output_tokens": 0}
        html = build_preview_html(session, detail)
        assert "A good summary" in html

    def test_html_escapes_user_content(self):
        session = {
            "project": "Test",
            "git_branch": "",
            "version": "",
            "message_count": 0,
            "created": "",
            "modified": "",
            "custom_title": "",
            "summary": "",
            "first_prompt": "",
        }
        detail = {
            "turns": [{"role": "user", "text": "<script>alert('xss')</script>"}],
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        html = build_preview_html(session, detail)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
