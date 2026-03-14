"""Tests for the enhancement mode loader module."""

from __future__ import annotations

import os


from voicetext.enhance.mode_loader import (
    _BUILTIN_MODES,
    ensure_default_modes,
    load_modes,
    parse_mode_file,
)


class TestParseModeFile:
    def test_parse_valid_file(self, tmp_path):
        f = tmp_path / "proofread.md"
        f.write_text(
            "---\nlabel: 纠错润色\norder: 10\n---\nYou are a proofreader.\n",
            encoding="utf-8",
        )
        result = parse_mode_file(str(f))
        assert result is not None
        assert result.mode_id == "proofread"
        assert result.label == "纠错润色"
        assert result.order == 10
        assert result.prompt == "You are a proofreader."

    def test_parse_missing_label(self, tmp_path):
        f = tmp_path / "custom.md"
        f.write_text("---\norder: 5\n---\nSome prompt.\n", encoding="utf-8")
        result = parse_mode_file(str(f))
        assert result is not None
        assert result.label == "custom"  # falls back to filename

    def test_parse_with_order(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("---\nlabel: Test\norder: 99\n---\nPrompt.\n", encoding="utf-8")
        result = parse_mode_file(str(f))
        assert result is not None
        assert result.order == 99

    def test_parse_no_front_matter(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("Just a plain prompt without front matter.", encoding="utf-8")
        result = parse_mode_file(str(f))
        assert result is not None
        assert result.label == "plain"
        assert result.order == 50
        assert result.prompt == "Just a plain prompt without front matter."

    def test_parse_empty_file(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("", encoding="utf-8")
        result = parse_mode_file(str(f))
        assert result is None

    def test_parse_whitespace_only_file(self, tmp_path):
        f = tmp_path / "blank.md"
        f.write_text("   \n  \n", encoding="utf-8")
        result = parse_mode_file(str(f))
        assert result is None

    def test_parse_front_matter_default_order(self, tmp_path):
        f = tmp_path / "noorder.md"
        f.write_text("---\nlabel: No Order\n---\nPrompt here.\n", encoding="utf-8")
        result = parse_mode_file(str(f))
        assert result is not None
        assert result.order == 50

    def test_parse_multiline_prompt(self, tmp_path):
        f = tmp_path / "multi.md"
        f.write_text(
            "---\nlabel: Multi\n---\nLine 1.\nLine 2.\nLine 3.\n",
            encoding="utf-8",
        )
        result = parse_mode_file(str(f))
        assert result is not None
        assert "Line 1." in result.prompt
        assert "Line 3." in result.prompt

    def test_parse_with_steps(self, tmp_path):
        f = tmp_path / "chain.md"
        f.write_text(
            "---\nlabel: Chain Mode\norder: 25\nsteps: proofread, translate_en\n---\n",
            encoding="utf-8",
        )
        result = parse_mode_file(str(f))
        assert result is not None
        assert result.steps == ["proofread", "translate_en"]
        assert result.label == "Chain Mode"
        assert result.order == 25

    def test_parse_without_steps_has_empty_list(self, tmp_path):
        f = tmp_path / "nosteps.md"
        f.write_text(
            "---\nlabel: Normal\norder: 10\n---\nSome prompt.\n",
            encoding="utf-8",
        )
        result = parse_mode_file(str(f))
        assert result is not None
        assert result.steps == []

    def test_parse_steps_single_item(self, tmp_path):
        f = tmp_path / "single_step.md"
        f.write_text(
            "---\nlabel: Single Step\nsteps: proofread\n---\n",
            encoding="utf-8",
        )
        result = parse_mode_file(str(f))
        assert result is not None
        assert result.steps == ["proofread"]

    def test_parse_steps_with_extra_spaces(self, tmp_path):
        f = tmp_path / "spaced.md"
        f.write_text(
            "---\nlabel: Spaced\nsteps:  proofread , translate_en , commandline_master \n---\n",
            encoding="utf-8",
        )
        result = parse_mode_file(str(f))
        assert result is not None
        assert result.steps == ["proofread", "translate_en", "commandline_master"]


class TestLoadModes:
    def test_load_from_directory(self, tmp_path):
        (tmp_path / "a.md").write_text(
            "---\nlabel: Mode A\norder: 1\n---\nPrompt A\n", encoding="utf-8"
        )
        (tmp_path / "b.md").write_text(
            "---\nlabel: Mode B\norder: 2\n---\nPrompt B\n", encoding="utf-8"
        )
        modes = load_modes(str(tmp_path))
        assert len(modes) == 2
        assert "a" in modes
        assert "b" in modes
        assert modes["a"].label == "Mode A"
        assert modes["b"].label == "Mode B"

    def test_load_empty_directory_returns_builtins(self, tmp_path):
        modes = load_modes(str(tmp_path))
        assert len(modes) == len(_BUILTIN_MODES)
        for key in _BUILTIN_MODES:
            assert key in modes

    def test_load_nonexistent_directory_returns_builtins(self, tmp_path):
        modes = load_modes(str(tmp_path / "nonexistent"))
        assert len(modes) == len(_BUILTIN_MODES)

    def test_load_ignores_non_md_files(self, tmp_path):
        (tmp_path / "valid.md").write_text(
            "---\nlabel: Valid\n---\nPrompt\n", encoding="utf-8"
        )
        (tmp_path / "readme.txt").write_text("Not a mode file", encoding="utf-8")
        (tmp_path / "notes.json").write_text("{}", encoding="utf-8")
        modes = load_modes(str(tmp_path))
        assert len(modes) == 1
        assert "valid" in modes

    def test_load_order_sorting(self, tmp_path):
        (tmp_path / "z_last.md").write_text(
            "---\nlabel: Last\norder: 99\n---\nPrompt\n", encoding="utf-8"
        )
        (tmp_path / "a_first.md").write_text(
            "---\nlabel: First\norder: 1\n---\nPrompt\n", encoding="utf-8"
        )
        (tmp_path / "m_mid.md").write_text(
            "---\nlabel: Mid\norder: 50\n---\nPrompt\n", encoding="utf-8"
        )
        modes = load_modes(str(tmp_path))
        from voicetext.enhance.mode_loader import get_sorted_modes

        sorted_list = get_sorted_modes(modes)
        assert sorted_list[0][0] == "a_first"
        assert sorted_list[1][0] == "m_mid"
        assert sorted_list[2][0] == "z_last"


class TestEnsureDefaultModes:
    def test_creates_files_in_empty_dir(self, tmp_path):
        modes_dir = str(tmp_path / "modes")
        result_path = ensure_default_modes(modes_dir)
        assert os.path.isdir(result_path)
        md_files = [f for f in os.listdir(result_path) if f.endswith(".md")]
        assert len(md_files) == len(_BUILTIN_MODES)

    def test_no_overwrite_existing_files(self, tmp_path):
        modes_dir = str(tmp_path / "modes")
        os.makedirs(modes_dir)
        # Pre-create proofread.md with custom content
        custom_content = "---\nlabel: My Custom\n---\nMy custom prompt.\n"
        proofread_file = os.path.join(modes_dir, "proofread.md")
        with open(proofread_file, "w", encoding="utf-8") as f:
            f.write(custom_content)

        ensure_default_modes(modes_dir)
        # proofread.md should keep its custom content
        with open(proofread_file, "r", encoding="utf-8") as f:
            assert f.read() == custom_content
        # Other builtins should have been created
        md_files = sorted(f for f in os.listdir(modes_dir) if f.endswith(".md"))
        assert len(md_files) == len(_BUILTIN_MODES)

    def test_creates_missing_builtins_alongside_custom(self, tmp_path):
        modes_dir = str(tmp_path / "modes")
        os.makedirs(modes_dir)
        # Only a custom file, no builtins
        custom_file = os.path.join(modes_dir, "custom.md")
        with open(custom_file, "w", encoding="utf-8") as f:
            f.write("---\nlabel: Custom\n---\nMy prompt.\n")

        ensure_default_modes(modes_dir)
        md_files = [f for f in os.listdir(modes_dir) if f.endswith(".md")]
        # custom + builtins
        assert len(md_files) == len(_BUILTIN_MODES) + 1
        assert "custom.md" in md_files

    def test_created_files_are_parseable(self, tmp_path):
        modes_dir = str(tmp_path / "modes")
        ensure_default_modes(modes_dir)
        modes = load_modes(modes_dir)
        assert len(modes) == len(_BUILTIN_MODES)
        for mode_id, mode_def in modes.items():
            assert mode_def.label
            assert mode_def.prompt


class TestBuiltinModes:
    def test_builtin_contains_all_modes(self):
        expected = {"proofread", "translate_en", "translate_en_plus", "commandline_master"}
        assert set(_BUILTIN_MODES.keys()) == expected

    def test_builtin_modes_have_labels(self):
        for mode_id, mode_def in _BUILTIN_MODES.items():
            assert mode_def.label, f"Mode {mode_id} missing label"

    def test_builtin_modes_have_prompts(self):
        for mode_id, mode_def in _BUILTIN_MODES.items():
            # Chain modes have empty prompts
            if mode_def.steps:
                assert mode_def.prompt == "", f"Chain mode {mode_id} should have empty prompt"
            else:
                assert mode_def.prompt, f"Mode {mode_id} missing prompt"

    def test_builtin_modes_have_unique_orders(self):
        orders = [m.order for m in _BUILTIN_MODES.values()]
        assert len(orders) == len(set(orders))

    def test_translate_en_plus_chain_mode(self):
        mode = _BUILTIN_MODES["translate_en_plus"]
        assert mode.label == "润色+翻译EN"
        assert mode.steps == ["proofread", "translate_en"]
        assert mode.order == 25
        assert mode.prompt == ""


class TestAddModeTemplate:
    """Verify the add-mode template used in the UI is parseable."""

    def test_template_is_parseable(self, tmp_path):
        from voicetext.controllers.enhance_mode_controller import EnhanceModeController

        template = EnhanceModeController._ADD_MODE_TEMPLATE
        f = tmp_path / "template.md"
        f.write_text(template, encoding="utf-8")
        result = parse_mode_file(str(f))
        assert result is not None
        assert result.label == "My New Mode"
        assert result.order == 60
        assert "helpful assistant" in result.prompt
