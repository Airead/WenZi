"""Tests for input_context module."""

from unittest.mock import MagicMock, patch


class TestInputContext:
    """Tests for InputContext dataclass and formatting methods."""

    def test_default_all_none(self):
        from wenzi.input_context import InputContext
        ctx = InputContext()
        assert ctx.app_name is None
        assert ctx.bundle_id is None
        assert ctx.window_title is None
        assert ctx.focused_role is None
        assert ctx.focused_description is None
        assert ctx.browser_domain is None

    def test_format_for_prompt_off(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")
        assert ctx.format_for_prompt("off") is None

    def test_format_for_prompt_basic(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")
        result = ctx.format_for_prompt("basic")
        assert "Terminal" in result
        assert "com.apple.Terminal" not in result  # bundle_id never in prompt

    def test_format_for_prompt_basic_no_app_name(self):
        from wenzi.input_context import InputContext
        ctx = InputContext()
        assert ctx.format_for_prompt("basic") is None

    def test_format_for_prompt_detailed(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(
            app_name="Google Chrome",
            bundle_id="com.google.Chrome",
            window_title="GitHub - PR #42",
            focused_role="AXTextArea",
            focused_description="Comment body",
            browser_domain="github.com",
        )
        result = ctx.format_for_prompt("detailed")
        assert "Google Chrome" in result
        assert "GitHub - PR #42" in result
        assert "AXTextArea" in result
        assert "github.com" in result
        assert "com.google.Chrome" not in result

    def test_format_for_prompt_detailed_partial_fields(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")
        result = ctx.format_for_prompt("detailed")
        assert "Terminal" in result
        # Should not crash with None fields

    def test_format_for_display(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(
            app_name="VS Code",
            window_title="main.py",
            focused_role="AXTextArea",
        )
        result = ctx.format_for_display()
        assert "VS Code" in result
        assert "main.py" in result
        assert "AXTextArea" in result

    def test_to_dict_omits_none(self):
        from wenzi.input_context import InputContext
        ctx = InputContext(app_name="Terminal", bundle_id="com.apple.Terminal")
        d = ctx.to_dict()
        assert d == {"app_name": "Terminal", "bundle_id": "com.apple.Terminal"}
        assert "window_title" not in d

    def test_from_dict(self):
        from wenzi.input_context import InputContext
        d = {"app_name": "Terminal", "bundle_id": "com.apple.Terminal", "focused_role": "AXTextArea"}
        ctx = InputContext.from_dict(d)
        assert ctx.app_name == "Terminal"
        assert ctx.bundle_id == "com.apple.Terminal"
        assert ctx.focused_role == "AXTextArea"
        assert ctx.window_title is None

    def test_from_dict_none(self):
        from wenzi.input_context import InputContext
        assert InputContext.from_dict(None) is None

    def test_from_dict_empty(self):
        from wenzi.input_context import InputContext
        ctx = InputContext.from_dict({})
        assert ctx is not None
        assert ctx.app_name is None


class TestCaptureInputContext:
    """Tests for capture_input_context() function."""

    def test_off_returns_none(self):
        from wenzi.input_context import capture_input_context
        assert capture_input_context("off") is None

    @patch("wenzi.input_context.get_frontmost_app")
    def test_basic_collects_app_only(self, mock_gfa):
        from wenzi.input_context import capture_input_context
        app = MagicMock()
        app.localizedName.return_value = "Terminal"
        app.bundleIdentifier.return_value = "com.apple.Terminal"
        app.processIdentifier.return_value = 1234
        mock_gfa.return_value = app
        ctx = capture_input_context("basic")
        assert ctx is not None
        assert ctx.app_name == "Terminal"
        assert ctx.bundle_id == "com.apple.Terminal"
        assert ctx.window_title is None
        assert ctx.focused_role is None

    @patch("wenzi.input_context._collect_ax_fields")
    @patch("wenzi.input_context.get_frontmost_app")
    def test_detailed_collects_all(self, mock_gfa, mock_collect):
        from wenzi.input_context import capture_input_context
        app = MagicMock()
        app.localizedName.return_value = "Terminal"
        app.bundleIdentifier.return_value = "com.apple.Terminal"
        app.processIdentifier.return_value = 1234
        mock_gfa.return_value = app
        mock_collect.return_value = ("zsh", "AXTextArea", None, None)
        ctx = capture_input_context("detailed")
        assert ctx is not None
        assert ctx.app_name == "Terminal"
        assert ctx.window_title == "zsh"
        assert ctx.focused_role == "AXTextArea"

    @patch("wenzi.input_context.get_frontmost_app")
    def test_returns_none_when_no_app(self, mock_gfa):
        from wenzi.input_context import capture_input_context
        mock_gfa.return_value = None
        assert capture_input_context("basic") is None

    @patch("wenzi.input_context._collect_ax_fields")
    @patch("wenzi.input_context.get_frontmost_app")
    def test_detailed_browser_domain(self, mock_gfa, mock_collect):
        from wenzi.input_context import capture_input_context
        app = MagicMock()
        app.localizedName.return_value = "Google Chrome"
        app.bundleIdentifier.return_value = "com.google.Chrome"
        app.processIdentifier.return_value = 5678
        mock_gfa.return_value = app
        mock_collect.return_value = (
            "GitHub - My Repo", "AXTextField", "Search or type a URL", "github.com"
        )
        ctx = capture_input_context("detailed")
        assert ctx.browser_domain == "github.com"
        assert ctx.focused_description == "Search or type a URL"

    @patch("wenzi.input_context.get_frontmost_app")
    def test_invalid_level_treated_as_basic(self, mock_gfa):
        from wenzi.input_context import capture_input_context
        app = MagicMock()
        app.localizedName.return_value = "Terminal"
        app.bundleIdentifier.return_value = "com.apple.Terminal"
        app.processIdentifier.return_value = 1234
        mock_gfa.return_value = app
        ctx = capture_input_context("invalid")
        assert ctx is not None
        assert ctx.app_name == "Terminal"
        assert ctx.window_title is None  # basic level

    def test_timeout_returns_partial_context(self):
        """AX timeout should gracefully degrade to partial context."""
        from wenzi.input_context import capture_input_context
        app = MagicMock()
        app.localizedName.return_value = "Terminal"
        app.bundleIdentifier.return_value = "com.apple.Terminal"
        app.processIdentifier.return_value = 1234

        with patch("wenzi.input_context.get_frontmost_app", return_value=app), \
             patch("wenzi.input_context._collect_ax_fields", side_effect=Exception("timeout")):
            ctx = capture_input_context("detailed")
            assert ctx is not None
            assert ctx.app_name == "Terminal"
            assert ctx.focused_role is None  # AX failed


class TestParseDomainFromTitle:
    def test_chrome_title(self):
        from wenzi.input_context import _parse_domain_from_title
        assert _parse_domain_from_title("GitHub - Google Chrome") is None  # "GitHub" is not a domain

    def test_bare_domain(self):
        from wenzi.input_context import _parse_domain_from_title
        assert _parse_domain_from_title("github.com") == "github.com"

    def test_url_title(self):
        from wenzi.input_context import _parse_domain_from_title
        assert _parse_domain_from_title("https://github.com/foo/bar") == "github.com"

    def test_firefox_title(self):
        from wenzi.input_context import _parse_domain_from_title
        assert _parse_domain_from_title("GitHub -- Mozilla Firefox") is None

    def test_non_domain(self):
        from wenzi.input_context import _parse_domain_from_title
        assert _parse_domain_from_title("My Document") is None

    def test_domain_after_chrome_strip(self):
        from wenzi.input_context import _parse_domain_from_title
        assert _parse_domain_from_title("github.com - Google Chrome") == "github.com"


