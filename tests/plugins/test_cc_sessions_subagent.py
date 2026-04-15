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
    lines = [
        json.dumps(
            {
                "type": "user",
                "agentId": "abc123def",
                "message": {"role": "user", "content": "test prompt"},
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "agentId": "abc123def",
                "message": {"role": "assistant", "model": "claude-haiku-4-5-20251001", "content": [{"type": "text", "text": "ok"}]},
            }
        ),
    ]
    agent1_jsonl.write_text("\n".join(lines) + "\n")

    agent1_meta = subagents_dir / "agent-abc123def.meta.json"
    agent1_meta.write_text(json.dumps({"agentType": "Explore", "description": "Code reuse review"}))

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
            "aaa-bbb-ccc",
            "subagents",
            "agent-nonexistent.jsonl",
        )
        assert result == expected


class TestCheckSubagentExists:
    def test_existing_and_missing(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _check_subagent_exists

        fix = _make_subagent_fixture(tmp_path)
        result = _check_subagent_exists(fix["parent_path"], ["abc123def", "missing999"])
        assert result["abc123def"]["exists"] is True
        assert result["abc123def"]["model"] == "claude-haiku-4-5-20251001"
        assert result["missing999"]["exists"] is False
        assert result["missing999"]["model"] == ""

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
            json.dumps(
                {
                    "type": "user",
                    "agentId": "abc",
                    "cwd": "/work/project",
                    "version": "2.1.81",
                    "message": {"role": "user", "content": "do stuff"},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "agentId": "abc",
                    "message": {"role": "assistant", "model": "claude-haiku-4-5-20251001", "content": [{"type": "text", "text": "ok"}]},
                }
            ),
        ]
        jsonl_path.write_text("\n".join(lines) + "\n")

        meta = _parse_subagent_meta(str(jsonl_path))
        assert meta["cwd"] == "/work/project"
        assert meta["version"] == "2.1.81"
        assert meta["model"] == "claude-haiku-4-5-20251001"

    def test_missing_fields_return_defaults(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _parse_subagent_meta

        jsonl_path = tmp_path / "agent-test.jsonl"
        jsonl_path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")

        meta = _parse_subagent_meta(str(jsonl_path))
        assert meta["cwd"] == ""
        assert meta["version"] == ""
        assert meta["model"] == ""

    def test_nonexistent_file(self):
        from plugins.cc_sessions.init_plugin import _parse_subagent_meta

        meta = _parse_subagent_meta("/nonexistent/path.jsonl")
        assert meta["cwd"] == ""
        assert meta["model"] == ""


class TestListSubagents:
    def test_lists_subagents_from_meta_and_jsonl(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _list_subagents

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        session_jsonl = project_dir / "sess-123.jsonl"
        session_jsonl.write_text("")
        subagents_dir = project_dir / "sess-123" / "subagents"
        subagents_dir.mkdir(parents=True)

        # Subagent 1: with meta.json
        (subagents_dir / "agent-abc.jsonl").write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "model": "claude-sonnet-4", "content": [{"type": "text", "text": "ok"}]},
                }
            )
            + "\n"
        )
        (subagents_dir / "agent-abc.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Find code"}))

        # Subagent 2: missing meta.json model, should fallback to jsonl
        (subagents_dir / "agent-def.jsonl").write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "model": "claude-haiku-4", "content": [{"type": "text", "text": "ok"}]},
                }
            )
            + "\n"
        )
        (subagents_dir / "agent-def.meta.json").write_text(json.dumps({"agentType": "general-purpose", "description": "Review diff"}))

        # Orphan meta (no jsonl) should be ignored
        (subagents_dir / "agent-orphan.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Orphan"}))

        result = _list_subagents(str(session_jsonl))
        result.sort(key=lambda x: x["agent_id"])

        assert len(result) == 2
        assert result[0]["agent_id"] == "abc"
        assert result[0]["description"] == "Find code"
        assert result[0]["agent_type"] == "Explore"
        assert result[0]["model"] == "claude-sonnet-4"
        assert result[1]["agent_id"] == "def"
        assert result[1]["description"] == "Review diff"
        assert result[1]["agent_type"] == "general-purpose"
        assert result[1]["model"] == "claude-haiku-4"

    def test_empty_when_no_subagents_dir(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _list_subagents

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        session_jsonl = project_dir / "sess-456.jsonl"
        session_jsonl.write_text("")

        result = _list_subagents(str(session_jsonl))
        assert result == []

    def test_skips_non_meta_files(self, tmp_path):
        from plugins.cc_sessions.init_plugin import _list_subagents

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        session_jsonl = project_dir / "sess-789.jsonl"
        session_jsonl.write_text("")
        subagents_dir = project_dir / "sess-789" / "subagents"
        subagents_dir.mkdir(parents=True)

        (subagents_dir / "agent-xyz.jsonl").write_text("{}")
        (subagents_dir / "agent-xyz.txt").write_text("not meta")

        result = _list_subagents(str(session_jsonl))
        assert result == []
