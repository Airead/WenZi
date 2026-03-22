"""Tests for cc-sessions subagent bridge helpers."""

import json
import os


def _make_subagent_fixture(tmp_path):
    """Create a parent session with subagent files for testing."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Parent session JSONL
    parent_jsonl = project_dir / "aaa-bbb-ccc.jsonl"
    parent_jsonl.write_text("")

    # Subagent directory and files
    subagents_dir = project_dir / "aaa-bbb-ccc" / "subagents"
    subagents_dir.mkdir(parents=True)

    agent1_jsonl = subagents_dir / "agent-abc123def.jsonl"
    agent1_jsonl.write_text(json.dumps({
        "type": "user",
        "agentId": "abc123def",
        "message": {"role": "user", "content": "test prompt"},
    }) + "\n")

    agent1_meta = subagents_dir / "agent-abc123def.meta.json"
    agent1_meta.write_text(json.dumps({"agentType": "Explore"}))

    return {
        "parent_path": str(parent_jsonl),
        "agent1_id": "abc123def",
        "agent1_path": str(agent1_jsonl),
    }


class TestResolveSubagentPath:
    def test_resolves_correct_path(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _resolve_subagent_path

        fix = _make_subagent_fixture(tmp_path)
        result = _resolve_subagent_path(fix["parent_path"], "abc123def")
        assert result == fix["agent1_path"]

    def test_nonexistent_agent_id(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _resolve_subagent_path

        fix = _make_subagent_fixture(tmp_path)
        result = _resolve_subagent_path(fix["parent_path"], "nonexistent")
        expected = os.path.join(
            os.path.dirname(fix["parent_path"]),
            "aaa-bbb-ccc", "subagents", "agent-nonexistent.jsonl",
        )
        assert result == expected


class TestCheckSubagentExists:
    def test_existing_and_missing(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _check_subagent_exists

        fix = _make_subagent_fixture(tmp_path)
        result = _check_subagent_exists(
            fix["parent_path"], ["abc123def", "missing999"]
        )
        assert result == {"abc123def": True, "missing999": False}

    def test_empty_list(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _check_subagent_exists

        fix = _make_subagent_fixture(tmp_path)
        result = _check_subagent_exists(fix["parent_path"], [])
        assert result == {}


class TestParseSubagentMeta:
    def test_extracts_basic_fields(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _parse_subagent_meta

        jsonl_path = tmp_path / "agent-test.jsonl"
        lines = [
            json.dumps({"type": "user", "agentId": "abc", "cwd": "/work/project",
                         "version": "2.1.81", "message": {"role": "user", "content": "do stuff"}}),
            json.dumps({"type": "assistant", "agentId": "abc",
                         "message": {"role": "assistant", "model": "claude-haiku-4-5-20251001",
                                     "content": [{"type": "text", "text": "ok"}]}}),
        ]
        jsonl_path.write_text("\n".join(lines) + "\n")

        meta = _parse_subagent_meta(str(jsonl_path))
        assert meta["cwd"] == "/work/project"
        assert meta["version"] == "2.1.81"

    def test_missing_fields_return_defaults(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _parse_subagent_meta

        jsonl_path = tmp_path / "agent-test.jsonl"
        jsonl_path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")

        meta = _parse_subagent_meta(str(jsonl_path))
        assert meta["cwd"] == ""
        assert meta["version"] == ""

    def test_nonexistent_file(self):
        from plugins.cc_sessions.init_plugin import _parse_subagent_meta

        meta = _parse_subagent_meta("/nonexistent/path.jsonl")
        assert meta["cwd"] == ""
