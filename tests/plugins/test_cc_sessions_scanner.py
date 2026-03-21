"""Tests for cc_sessions.scanner module."""

from __future__ import annotations

import json
from pathlib import Path

from cc_sessions.scanner import (
    SessionScanner,
    _choose_title,
    _project_name_from_dir,
    _scan_project_with_index,
    _scan_session_jsonl,
)


# ---------------------------------------------------------------------------
# _project_name_from_dir
# ---------------------------------------------------------------------------


class TestProjectNameFromDir:
    def test_simple_path(self):
        assert _project_name_from_dir("-Users-fanrenhao-work-VoiceText") == "VoiceText"

    def test_nested_path(self):
        assert _project_name_from_dir("-Users-alice-projects-deep-nested-repo") == "repo"

    def test_single_segment(self):
        assert _project_name_from_dir("myproject") == "myproject"

    def test_leading_trailing_dashes(self):
        assert _project_name_from_dir("--foo--") == "foo"


# ---------------------------------------------------------------------------
# _choose_title
# ---------------------------------------------------------------------------


class TestChooseTitle:
    def test_custom_title_wins(self):
        assert _choose_title("Custom", "Summary", "Prompt") == "Custom"

    def test_summary_fallback(self):
        assert _choose_title(None, "Summary", "Prompt") == "Summary"

    def test_first_prompt_fallback(self):
        assert _choose_title(None, None, "Prompt") == "Prompt"

    def test_empty_when_all_none(self):
        assert _choose_title(None, None, None) == ""

    def test_truncation_at_80(self):
        long_text = "A" * 100
        result = _choose_title(long_text, None, None)
        assert len(result) == 80
        assert result.endswith("...")


# ---------------------------------------------------------------------------
# _scan_project_with_index
# ---------------------------------------------------------------------------


class TestScanProjectWithIndex:
    def test_reads_index(self, tmp_path: Path):
        proj = tmp_path / "-Users-me-work-Proj"
        proj.mkdir()
        index = [
            {
                "sessionId": "s1",
                "fullPath": str(proj / "s1.jsonl"),
                "firstPrompt": "Hello world",
                "summary": None,
                "customTitle": None,
                "messageCount": 5,
                "created": "2026-01-01T00:00:00Z",
                "modified": "2026-01-01T01:00:00Z",
                "gitBranch": "main",
                "projectPath": "/Users/me/work/Proj",
            }
        ]
        (proj / "sessions-index.json").write_text(json.dumps(index))

        results = _scan_project_with_index(proj, "Proj")
        assert len(results) == 1
        s = results[0]
        assert s["session_id"] == "s1"
        assert s["project"] == "Proj"
        assert s["title"] == "Hello world"
        assert s["message_count"] == 5

    def test_title_priority_custom_over_summary(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        index = [
            {
                "sessionId": "s2",
                "firstPrompt": "prompt",
                "summary": "summary",
                "customTitle": "custom",
            }
        ]
        (proj / "sessions-index.json").write_text(json.dumps(index))

        results = _scan_project_with_index(proj, "Proj")
        assert results[0]["title"] == "custom"

    def test_title_priority_summary_over_prompt(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        index = [
            {
                "sessionId": "s3",
                "firstPrompt": "prompt",
                "summary": "summary",
                "customTitle": None,
            }
        ]
        (proj / "sessions-index.json").write_text(json.dumps(index))

        results = _scan_project_with_index(proj, "Proj")
        assert results[0]["title"] == "summary"

    def test_missing_index_returns_empty(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        assert _scan_project_with_index(proj, "Proj") == []

    def test_corrupt_index_returns_empty(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "sessions-index.json").write_text("{broken json")
        assert _scan_project_with_index(proj, "Proj") == []


# ---------------------------------------------------------------------------
# _scan_session_jsonl
# ---------------------------------------------------------------------------


class TestScanSessionJsonl:
    def _make_jsonl(self, path: Path, lines: list[dict]) -> Path:
        path.write_text("\n".join(json.dumps(item) for item in lines) + "\n")
        return path

    def test_parses_first_user_message(self, tmp_path: Path):
        jsonl = self._make_jsonl(
            tmp_path / "abc-123.jsonl",
            [
                {"type": "system", "timestamp": "2026-01-01T00:00:00Z", "cwd": "/tmp/proj", "version": "2.1.0", "gitBranch": "dev"},
                {"type": "user", "timestamp": "2026-01-01T00:00:01Z", "message": {"content": "Help me refactor"}},
            ],
        )
        result = _scan_session_jsonl(jsonl, "Proj")
        assert result is not None
        assert result["session_id"] == "abc-123"
        assert result["first_prompt"] == "Help me refactor"
        assert result["title"] == "Help me refactor"
        assert result["cwd"] == "/tmp/proj"
        assert result["version"] == "2.1.0"
        assert result["git_branch"] == "dev"

    def test_content_parts_list(self, tmp_path: Path):
        jsonl = self._make_jsonl(
            tmp_path / "sess.jsonl",
            [
                {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": [{"text": "Part A"}, {"text": "Part B"}]}},
            ],
        )
        result = _scan_session_jsonl(jsonl, "Proj")
        assert result is not None
        assert result["first_prompt"] == "Part A Part B"

    def test_no_user_message_returns_none(self, tmp_path: Path):
        jsonl = self._make_jsonl(
            tmp_path / "empty.jsonl",
            [
                {"type": "system", "data": "init"},
            ],
        )
        result = _scan_session_jsonl(jsonl, "Proj")
        # No timestamp and no user message -> None
        assert result is None

    def test_corrupt_jsonl_handled(self, tmp_path: Path):
        path = tmp_path / "bad.jsonl"
        path.write_text("{broken\n{also broken\n")
        result = _scan_session_jsonl(path, "Proj")
        assert result is None

    def test_empty_file_returns_none(self, tmp_path: Path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        result = _scan_session_jsonl(path, "Proj")
        assert result is None


# ---------------------------------------------------------------------------
# SessionScanner.scan_all
# ---------------------------------------------------------------------------


class TestSessionScanner:
    def test_scan_all_with_index(self, tmp_path: Path):
        proj = tmp_path / "-Users-me-work-MyApp"
        proj.mkdir()
        index = [
            {
                "sessionId": "s1",
                "fullPath": str(proj / "s1.jsonl"),
                "firstPrompt": "Hello",
                "summary": None,
                "customTitle": None,
                "messageCount": 3,
                "created": "2026-01-01T00:00:00Z",
                "modified": "2026-01-02T00:00:00Z",
                "gitBranch": "main",
                "projectPath": "/Users/me/work/MyApp",
            }
        ]
        (proj / "sessions-index.json").write_text(json.dumps(index))

        scanner = SessionScanner(base_dir=tmp_path, cache_path=None)
        sessions = scanner.scan_all()
        assert len(sessions) == 1
        assert sessions[0]["project"] == "MyApp"

    def test_scan_all_fallback_jsonl(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        jsonl = proj / "sess1.jsonl"
        jsonl.write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": "Hi"}}) + "\n"
        )

        scanner = SessionScanner(base_dir=tmp_path, cache_path=None)
        sessions = scanner.scan_all()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess1"

    def test_cache_hit_on_same_mtime(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        jsonl = proj / "cached.jsonl"
        jsonl.write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": "First"}}) + "\n"
        )

        scanner = SessionScanner(base_dir=tmp_path, cache_path=tmp_path / "cache.json")
        results1 = scanner.scan_all()
        assert len(results1) == 1

        # Second scan — same mtime, should hit cache
        results2 = scanner.scan_all()
        assert len(results2) == 1
        assert results2[0]["first_prompt"] == "First"

        # Verify cache was used by checking internal state
        cache_key = str(jsonl)
        assert scanner._cache is not None
        assert scanner._cache.get(cache_key) is not None

    def test_sorted_by_modified_desc(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        index = [
            {
                "sessionId": "old",
                "firstPrompt": "Old",
                "modified": "2026-01-01T00:00:00Z",
            },
            {
                "sessionId": "new",
                "firstPrompt": "New",
                "modified": "2026-01-02T00:00:00Z",
            },
        ]
        (proj / "sessions-index.json").write_text(json.dumps(index))

        scanner = SessionScanner(base_dir=tmp_path, cache_path=None)
        sessions = scanner.scan_all()
        assert sessions[0]["session_id"] == "new"
        assert sessions[1]["session_id"] == "old"

    def test_nonexistent_base_dir(self, tmp_path: Path):
        scanner = SessionScanner(base_dir=tmp_path / "nope", cache_path=None)
        assert scanner.scan_all() == []


# ---------------------------------------------------------------------------
# SessionScanner with persistent disk cache
# ---------------------------------------------------------------------------


class TestSessionScannerPersistentCache:
    """Test SessionScanner with persistent disk cache."""

    def test_cache_persists_across_instances(self, tmp_path: Path):
        """A second scanner instance reads cached data without re-parsing."""
        proj = tmp_path / "projects" / "proj"
        proj.mkdir(parents=True)
        jsonl = proj / "persist1.jsonl"
        jsonl.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"content": "Persist test"},
            }) + "\n"
        )
        cache_path = tmp_path / "cache" / "cc_sessions_cache.json"

        scanner1 = SessionScanner(base_dir=tmp_path / "projects", cache_path=cache_path)
        sessions1 = scanner1.scan_all()
        assert len(sessions1) == 1
        assert sessions1[0]["first_prompt"] == "Persist test"

        # Second scanner — should read from disk cache
        scanner2 = SessionScanner(base_dir=tmp_path / "projects", cache_path=cache_path)
        sessions2 = scanner2.scan_all()
        assert len(sessions2) == 1
        assert sessions2[0]["first_prompt"] == "Persist test"

    def test_detects_new_session(self, tmp_path: Path):
        """New JSONL file not in cache is parsed and added."""
        proj = tmp_path / "projects" / "proj"
        proj.mkdir(parents=True)
        cache_path = tmp_path / "cache" / "cc_sessions_cache.json"

        scanner = SessionScanner(base_dir=tmp_path / "projects", cache_path=cache_path)
        assert scanner.scan_all() == []

        # Add a new session file
        jsonl = proj / "new1.jsonl"
        jsonl.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"content": "New session"},
            }) + "\n"
        )
        sessions = scanner.scan_all()
        assert len(sessions) == 1
        assert sessions[0]["first_prompt"] == "New session"

    def test_detects_modified_session(self, tmp_path: Path):
        """Modified JSONL file (mtime changed) is re-parsed."""
        proj = tmp_path / "projects" / "proj"
        proj.mkdir(parents=True)
        jsonl = proj / "mod1.jsonl"
        jsonl.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"content": "Original"},
            }) + "\n"
        )
        cache_path = tmp_path / "cache" / "cc_sessions_cache.json"

        scanner = SessionScanner(base_dir=tmp_path / "projects", cache_path=cache_path)
        sessions = scanner.scan_all()
        assert sessions[0]["first_prompt"] == "Original"

        # Modify the file (change mtime)
        import time
        time.sleep(0.05)
        jsonl.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"content": "Updated"},
            }) + "\n"
        )
        sessions = scanner.scan_all()
        assert sessions[0]["first_prompt"] == "Updated"

    def test_prunes_deleted_session(self, tmp_path: Path):
        """Deleted JSONL file is removed from cache."""
        proj = tmp_path / "projects" / "proj"
        proj.mkdir(parents=True)
        jsonl = proj / "del1.jsonl"
        jsonl.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"content": "Will be deleted"},
            }) + "\n"
        )
        cache_path = tmp_path / "cache" / "cc_sessions_cache.json"

        scanner = SessionScanner(base_dir=tmp_path / "projects", cache_path=cache_path)
        assert len(scanner.scan_all()) == 1

        # Delete the file
        jsonl.unlink()
        sessions = scanner.scan_all()
        assert len(sessions) == 0

    def test_no_cache_path_uses_memory_only(self, tmp_path: Path):
        """When cache_path is None, scanner works without disk persistence."""
        proj = tmp_path / "projects" / "proj"
        proj.mkdir(parents=True)
        jsonl = proj / "mem1.jsonl"
        jsonl.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"content": "Memory only"},
            }) + "\n"
        )

        scanner = SessionScanner(base_dir=tmp_path / "projects", cache_path=None)
        sessions = scanner.scan_all()
        assert len(sessions) == 1
