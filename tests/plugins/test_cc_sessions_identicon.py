"""Tests for cc-sessions identicon generation."""

import base64
import xml.etree.ElementTree as ET

from cc_sessions.identicon import (
    COLORS,
    _djb2,
    _get_initials,
    generate,
)


def _decode_svg(uri: str) -> str:
    """Decode a data URI to its SVG string content."""
    b64 = uri.split(",", 1)[1]
    return base64.b64decode(b64).decode()


class TestDjb2:
    def test_known_value(self):
        assert _djb2("VoiceText") == 89843776

    def test_different_inputs_different_hashes(self):
        assert _djb2("VoiceText") != _djb2("claude-code")

    def test_empty_string(self):
        assert _djb2("") == 5381

    def test_unicode(self):
        h = _djb2("项目名")
        assert isinstance(h, int)
        assert h > 0


class TestGetInitials:
    def test_hyphen_separated(self):
        assert _get_initials("claude-code") == "Cc"
        assert _get_initials("api-server") == "As"
        assert _get_initials("ml-pipeline") == "Mp"

    def test_underscore_separated(self):
        assert _get_initials("my_project") == "Mp"

    def test_camel_case(self):
        assert _get_initials("VoiceText") == "Vt"
        assert _get_initials("WenZi") == "Wz"

    def test_plain_word(self):
        assert _get_initials("dotfiles") == "Do"
        assert _get_initials("blog") == "Bl"

    def test_single_char(self):
        assert _get_initials("x") == "X"

    def test_empty_string(self):
        assert _get_initials("") == "?"

    def test_multi_segment(self):
        assert _get_initials("infra-terraform") == "It"
        assert _get_initials("react-dashboard") == "Rd"

    def test_first_upper_second_lower(self):
        for name in ["VoiceText", "claude-code", "dotfiles", "WenZi"]:
            initials = _get_initials(name)
            assert initials[0].isupper(), f"{name} -> {initials}: first should be upper"
            if len(initials) > 1:
                assert initials[1].islower(), f"{name} -> {initials}: second should be lower"

    def test_leading_numbers_stripped(self):
        assert _get_initials("123abc") == "Ab"

    def test_dot_separated(self):
        assert _get_initials("my.project") == "Mp"
        assert _get_initials("org.company.app") == "Oc"

    def test_special_chars_only(self):
        # No alpha chars → letters strip is empty → final fallback to name[0]
        assert _get_initials("---") == "-"

    def test_leading_separator(self):
        assert _get_initials("-foo") == "Fo"


class TestGenerate:
    def test_deterministic(self):
        # Verify stable output across calls (guards against accidental randomness)
        first = generate("VoiceText")
        generate.cache_clear()
        assert generate("VoiceText") == first

    def test_different_projects_different_icons(self):
        names = ["VoiceText", "claude-code", "WenZi", "dotfiles",
                 "react-app", "api-server", "blog", "infra"]
        results = [generate(n) for n in names]
        assert len(set(results)) >= 6

    def test_data_uri_format(self):
        uri = generate("test-project")
        assert uri.startswith("data:image/svg+xml;base64,")

    def test_valid_svg(self):
        svg_str = _decode_svg(generate("test-project"))
        root = ET.fromstring(svg_str)
        assert root.tag == "{http://www.w3.org/2000/svg}svg"

    def test_empty_name(self):
        uri = generate("")
        assert uri.startswith("data:image/svg+xml;base64,")
        assert "?" in _decode_svg(uri)

    def test_long_name(self):
        uri = generate("a" * 1000)
        assert uri.startswith("data:image/svg+xml;base64,")

    def test_color_from_palette(self):
        for name in ["VoiceText", "claude-code", "WenZi"]:
            svg_str = _decode_svg(generate(name))
            assert any(c in svg_str for c in COLORS), (
                f"{name}: SVG should contain a color from the palette"
            )

    def test_initials_in_svg(self):
        cases = [
            ("claude-code", "Cc"),
            ("VoiceText", "Vt"),
            ("dotfiles", "Do"),
        ]
        for name, expected in cases:
            svg_str = _decode_svg(generate(name))
            assert expected in svg_str, f"{name} should have '{expected}' in SVG"
