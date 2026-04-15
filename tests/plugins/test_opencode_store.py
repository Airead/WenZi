"""Tests for cc_sessions.opencode_store module."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from cc_sessions import opencode_store as store


class TestSubagentHelpers:
    def test_parse_subagent_title(self):
        assert store._parse_subagent_title("desc (@explore subagent)") == ("desc", "explore")
        assert store._parse_subagent_title("plain title") == ("plain title", "")

    def test_list_and_check_subagents(self, tmp_path: Path, monkeypatch):
        db_path = tmp_path / "opencode.db"
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.cursor()
            cur.execute("CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT, version TEXT)")
            cur.execute("CREATE TABLE message (id INTEGER PRIMARY KEY, session_id TEXT, data TEXT, role TEXT)")
            cur.execute("INSERT INTO session VALUES ('parent1', NULL, 'Parent', 'v1')")
            cur.execute("INSERT INTO session VALUES ('child1', 'parent1', 'Explore code (@explore subagent)', 'v1')")
            cur.execute(
                "INSERT INTO message VALUES (1, 'child1', ?, 'assistant')",
                (json.dumps({"role": "assistant", "modelID": "m1"}),),
            )
        monkeypatch.setattr(store, "_db_path", lambda: db_path)

        subs = store.list_opencode_subagents("parent1")
        assert len(subs) == 1
        assert subs[0]["agent_id"] == "child1"
        assert subs[0]["description"] == "Explore code"
        assert subs[0]["agent_type"] == "explore"
        assert subs[0]["model"] == "m1"
        assert subs[0]["version"] == "v1"

        exists = store.check_opencode_subagent_exists("parent1", ["child1", "missing"])
        assert exists["child1"]["exists"] is True
        assert exists["child1"]["model"] == "m1"
        assert exists["missing"]["exists"] is False


class TestConvertMessage:
    def test_skips_non_user_assistant_roles(self):
        assert store._convert_message({"role": "system"}, 0, []) is None

    def test_user_text_only(self):
        line = store._convert_message(
            {"role": "user"},
            1_000_000,
            [{"type": "text", "text": "hello"}],
        )
        assert line == {
            "type": "user",
            "timestamp": "1970-01-01T00:16:40+00:00",
            "cwd": "",
            "message": {"content": "hello"},
        }

    def test_user_skips_synthetic_text(self):
        line = store._convert_message(
            {"role": "user"},
            0,
            [
                {"type": "text", "text": "The following tool was executed by the user", "synthetic": True},
                {"type": "text", "text": "real question"},
            ],
        )
        assert line["message"]["content"] == "real question"

    def test_user_returns_none_when_empty(self):
        assert store._convert_message({"role": "user"}, 0, []) is None
        assert (
            store._convert_message(
                {"role": "user"},
                0,
                [{"type": "text", "text": ""}],
            )
            is None
        )

    def test_assistant_text_only(self):
        line = store._convert_message(
            {"role": "assistant", "modelID": "m1"},
            0,
            [{"type": "text", "text": "hi"}],
        )
        assert line["type"] == "assistant"
        assert line["message"]["content"] == [{"type": "text", "text": "hi"}]
        assert line["message"]["model"] == "m1"

    def test_assistant_reasoning_to_thinking(self):
        line = store._convert_message(
            {"role": "assistant"},
            0,
            [{"type": "reasoning", "text": "let me think"}],
        )
        assert line["message"]["content"] == [{"type": "thinking", "thinking": "let me think"}]

    def test_assistant_tool_to_tool_use_and_result(self):
        parts = [
            {
                "type": "tool",
                "tool": "bash",
                "callID": "call_123",
                "state": {
                    "input": {"command": "ls"},
                    "output": "file.txt",
                    "title": "",
                },
            }
        ]
        line = store._convert_message({"role": "assistant"}, 0, parts)
        content = line["message"]["content"]
        assert content[0] == {
            "type": "tool_use",
            "id": "call_123",
            "name": "Bash",
            "input": {"command": "ls"},
        }
        assert content[1] == {
            "type": "tool_result",
            "tool_use_id": "call_123",
            "content": "file.txt",
        }

    def test_assistant_task_to_agent_subagent(self):
        parts = [
            {
                "type": "tool",
                "tool": "task",
                "callID": "c1",
                "state": {
                    "input": {
                        "description": "explore code",
                        "prompt": "do it",
                        "subagent_type": "explore",
                    },
                    "output": "task_id: abc\nresult",
                },
            }
        ]
        line = store._convert_message({"role": "assistant"}, 0, parts)
        content = line["message"]["content"]
        assert content[0]["type"] == "tool_use"
        assert content[0]["name"] == "Agent"
        assert content[0]["input"]["description"] == "explore code"
        assert content[0]["input"]["subagent_type"] == "explore"
        assert content[1]["type"] == "tool_result"

    def test_assistant_file_part(self):
        line = store._convert_message(
            {"role": "assistant"},
            0,
            [{"type": "file", "filename": "foo.py"}],
        )
        assert line["message"]["content"] == [{"type": "text", "text": "@foo.py"}]

    def test_assistant_patch_part(self):
        line = store._convert_message(
            {"role": "assistant"},
            0,
            [{"type": "patch", "files": ["a.py", "b.py"]}],
        )
        assert line["message"]["content"] == [{"type": "text", "text": "[Patch] a.py, b.py"}]

    def test_assistant_ignores_step_and_subtask(self):
        line = store._convert_message(
            {"role": "assistant"},
            0,
            [
                {"type": "step-start"},
                {"type": "step-finish"},
                {"type": "subtask", "prompt": "x"},
                {"type": "compaction", "auto": True},
                {"type": "text", "text": "ok"},
            ],
        )
        assert line["message"]["content"] == [{"type": "text", "text": "ok"}]

    def test_unknown_part_with_text(self):
        line = store._convert_message(
            {"role": "assistant"},
            0,
            [{"type": "custom", "text": "custom text"}],
        )
        assert line["message"]["content"] == [{"type": "text", "text": "custom text"}]

    def test_tool_name_mapping(self):
        assert store._map_tool_name("bash") == "Bash"
        assert store._map_tool_name("read") == "Read"
        assert store._map_tool_name("unknown") == "Unknown"

    def test_build_tool_input_adds_title_as_description(self):
        inp = store._build_tool_input("Bash", {"command": "ls"}, "List files")
        assert inp["command"] == "ls"
        assert inp["description"] == "List files"

    def test_empty_assistant_parts_yield_single_empty_text(self):
        line = store._convert_message({"role": "assistant"}, 0, [])
        assert line["message"]["content"] == [{"type": "text", "text": ""}]
