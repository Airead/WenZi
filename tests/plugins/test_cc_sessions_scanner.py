"""Tests for cc_sessions.scanner module."""

from __future__ import annotations

import json
from pathlib import Path

from cc_sessions.scanner import (
    SessionScanner,
    _choose_title,
    _clean_first_prompt,
    _find_git_root,
    _git_remote_name,
    _name_from_cwd,
    _project_name_from_dir,
    _resolve_project_name,
    _scan_session_jsonl,
    is_noise_message,
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
# _find_git_root
# ---------------------------------------------------------------------------


class TestFindGitRoot:
    def test_finds_git_at_cwd(self, tmp_path: Path):
        """CWD is the repo root itself."""
        (tmp_path / ".git").mkdir()
        assert _find_git_root(str(tmp_path)) == str(tmp_path)

    def test_finds_git_in_parent(self, tmp_path: Path):
        """CWD is a subdirectory of the repo."""
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "docs"
        sub.mkdir()
        assert _find_git_root(str(sub)) == str(tmp_path)

    def test_finds_git_multiple_levels_up(self, tmp_path: Path):
        """CWD is nested several levels deep."""
        (tmp_path / ".git").mkdir()
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        assert _find_git_root(str(deep)) == str(tmp_path)

    def test_returns_empty_when_no_git(self, tmp_path: Path):
        """No .git anywhere up to root."""
        sub = tmp_path / "no-repo" / "child"
        sub.mkdir(parents=True)
        assert _find_git_root(str(sub)) == ""

    def test_finds_git_file_worktree(self, tmp_path: Path):
        """CWD has a .git file (worktree), treated as git root."""
        (tmp_path / ".git").write_text("gitdir: /some/path\n")
        sub = tmp_path / "src"
        sub.mkdir()
        assert _find_git_root(str(sub)) == str(tmp_path)

    def test_innermost_repo_wins(self, tmp_path: Path):
        """Nested repos: inner .git found first."""
        (tmp_path / ".git").mkdir()
        inner = tmp_path / "submodule"
        inner.mkdir()
        (inner / ".git").mkdir()
        child = inner / "lib"
        child.mkdir()
        assert _find_git_root(str(child)) == str(inner)

    def test_nonexistent_path(self):
        """CWD that no longer exists on disk returns empty."""
        assert _find_git_root("/nonexistent/path/that/does/not/exist") == ""


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
# Index supplements (summary/customTitle merge)
# ---------------------------------------------------------------------------


class TestIndexSupplements:
    """Test that summary/customTitle from index are merged into JSONL-scanned sessions."""

    def _make_proj_with_index_and_jsonl(self, tmp_path, session_id, prompt, index_entry):
        """Helper: create a project dir with both a JSONL file and an index."""
        proj = tmp_path / "projects" / "proj"
        proj.mkdir(parents=True, exist_ok=True)
        jsonl = proj / f"{session_id}.jsonl"
        jsonl.write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                        "message": {"content": prompt}}) + "\n"
        )
        index_data = [{"sessionId": session_id, **index_entry}]
        (proj / "sessions-index.json").write_text(json.dumps(index_data))
        return tmp_path / "projects"

    def test_merges_summary_and_custom_title(self, tmp_path: Path):
        base = self._make_proj_with_index_and_jsonl(
            tmp_path, "s1", "Hello",
            {"summary": "A good summary", "customTitle": "My Title"},
        )
        scanner = SessionScanner(base_dir=base, cache_path=None)
        sessions = scanner.scan_all()
        assert len(sessions) == 1
        assert sessions[0]["summary"] == "A good summary"
        assert sessions[0]["custom_title"] == "My Title"
        assert sessions[0]["title"] == "My Title"  # customTitle wins

    def test_title_priority_summary_over_prompt(self, tmp_path: Path):
        base = self._make_proj_with_index_and_jsonl(
            tmp_path, "s2", "prompt text",
            {"summary": "summary text", "customTitle": ""},
        )
        scanner = SessionScanner(base_dir=base, cache_path=None)
        sessions = scanner.scan_all()
        assert sessions[0]["title"] == "summary text"

    def test_no_index_uses_first_prompt(self, tmp_path: Path):
        proj = tmp_path / "projects" / "proj"
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                        "message": {"content": "Just a prompt"}}) + "\n"
        )
        scanner = SessionScanner(base_dir=tmp_path / "projects", cache_path=None)
        sessions = scanner.scan_all()
        assert sessions[0]["title"] == "Just a prompt"
        assert sessions[0]["summary"] == ""

    def test_corrupt_index_ignored(self, tmp_path: Path):
        proj = tmp_path / "projects" / "proj"
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                        "message": {"content": "Hello"}}) + "\n"
        )
        (proj / "sessions-index.json").write_text("{broken json")
        scanner = SessionScanner(base_dir=tmp_path / "projects", cache_path=None)
        sessions = scanner.scan_all()
        assert len(sessions) == 1
        assert sessions[0]["title"] == "Hello"


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
    def test_scan_all_with_index_supplements(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "s1.jsonl").write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                        "cwd": "/Users/me/work/MyApp",
                        "message": {"content": "Hello"}}) + "\n"
        )
        index = [{"sessionId": "s1", "summary": "A summary", "customTitle": ""}]
        (proj / "sessions-index.json").write_text(json.dumps(index))

        scanner = SessionScanner(base_dir=tmp_path, cache_path=None)
        sessions = scanner.scan_all()
        assert len(sessions) == 1
        assert sessions[0]["summary"] == "A summary"

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
        (proj / "old.jsonl").write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                        "message": {"content": "Old"}}) + "\n"
        )
        (proj / "new.jsonl").write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-02T00:00:00Z",
                        "message": {"content": "New"}}) + "\n"
        )

        scanner = SessionScanner(base_dir=tmp_path, cache_path=None)
        sessions = scanner.scan_all()
        assert len(sessions) == 2
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


# ---------------------------------------------------------------------------
# Session metadata fields (summary, custom_title)
# ---------------------------------------------------------------------------


class TestSessionMetadataFields:
    """Test that summary and custom_title are extracted via index supplements."""

    def test_index_extracts_summary_and_custom_title(self, tmp_path: Path):
        proj = tmp_path / "projects" / "proj"
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                        "message": {"content": "Hello"}}) + "\n"
        )
        index = [{"sessionId": "s1", "summary": "Refactored config module",
                   "customTitle": "Config Refactor"}]
        (proj / "sessions-index.json").write_text(json.dumps(index))
        scanner = SessionScanner(base_dir=tmp_path / "projects", cache_path=None)
        sessions = scanner.scan_all()
        assert sessions[0]["summary"] == "Refactored config module"
        assert sessions[0]["custom_title"] == "Config Refactor"

    def test_index_empty_custom_title(self, tmp_path: Path):
        proj = tmp_path / "projects" / "proj"
        proj.mkdir(parents=True)
        (proj / "s2.jsonl").write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                        "message": {"content": "Hi"}}) + "\n"
        )
        index = [{"sessionId": "s2", "summary": "A summary", "customTitle": ""}]
        (proj / "sessions-index.json").write_text(json.dumps(index))
        scanner = SessionScanner(base_dir=tmp_path / "projects", cache_path=None)
        sessions = scanner.scan_all()
        assert sessions[0]["summary"] == "A summary"
        assert sessions[0]["custom_title"] == ""


class TestNoiseFiltering:
    """Test filtering of system-injected noise messages."""

    def test_is_noise_message_local_command_caveat(self):
        text = '<local-command-caveat>Caveat: The messages below were generated...</local-command-caveat>'
        assert is_noise_message(text) is True

    def test_is_noise_message_command_name(self):
        text = '<command-name>/clear</command-name> <command-message>clear</command-message>'
        assert is_noise_message(text) is True

    def test_is_noise_message_normal_text(self):
        assert is_noise_message("refactor the config module") is False

    def test_is_noise_message_empty(self):
        assert is_noise_message("") is False

    def test_clean_first_prompt_strips_tags(self):
        raw = '<command-name>/clear</command-name> <command-message>clear</command-message>'
        result = _clean_first_prompt(raw)
        assert "<" not in result
        assert result == "/clear clear"

    def test_clean_first_prompt_normal_text_unchanged(self):
        assert _clean_first_prompt("hello world") == "hello world"

    def test_choose_title_skips_noise_prompt(self):
        title = _choose_title(
            None,
            "A good summary",
            '<local-command-caveat>Caveat: noise</local-command-caveat>',
        )
        assert title == "A good summary"

    def test_scan_jsonl_skips_noise_first_message(self, tmp_path: Path):
        """Scanner skips noise messages and finds the real first prompt."""
        jsonl = tmp_path / "cleared.jsonl"
        jsonl.write_text("\n".join(json.dumps(obj) for obj in [
            {"type": "user", "timestamp": "2026-01-01T00:00:00Z",
             "message": {"content": "<local-command-caveat>Caveat: noise</local-command-caveat>"}},
            {"type": "user", "timestamp": "2026-01-01T00:00:01Z",
             "message": {"content": "<command-name>/clear</command-name>"}},
            {"type": "user", "timestamp": "2026-01-01T00:00:02Z",
             "message": {"content": "refactor the config module"}},
        ]) + "\n")
        result = _scan_session_jsonl(jsonl, "Proj")
        assert result is not None
        assert result["first_prompt"] == "refactor the config module"

    def test_index_supplements_title_from_summary_when_prompt_is_noise(self, tmp_path: Path):
        """When firstPrompt is noise, index summary is used for title."""
        proj = tmp_path / "projects" / "proj"
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                        "message": {"content": "<local-command-caveat>noise</local-command-caveat>"}}) + "\n"
            + json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:01Z",
                          "message": {"content": "real question"}}) + "\n"
        )
        index = [{"sessionId": "s1", "summary": "Real summary", "customTitle": ""}]
        (proj / "sessions-index.json").write_text(json.dumps(index))
        scanner = SessionScanner(base_dir=tmp_path / "projects", cache_path=None)
        sessions = scanner.scan_all()
        assert sessions[0]["title"] == "Real summary"


class TestUserMessageCounting:
    """Test that _scan_session_jsonl counts real user messages correctly."""

    def test_counts_real_user_messages(self, tmp_path: Path):
        jsonl = tmp_path / "s.jsonl"
        jsonl.write_text(
            '{"type":"system","timestamp":"2026-01-01T00:00:00Z"}\n'
            '{"type":"user","timestamp":"2026-01-01T00:00:01Z","message":{"content":"Hi"}}\n'
            '{"type":"assistant","message":{"content":"Hello"}}\n'
            '{"type":"user","timestamp":"2026-01-01T00:00:02Z","message":{"content":"Bye"}}\n'
            '{"type":"progress","data":{}}\n'
        )
        result = _scan_session_jsonl(jsonl, "P")
        assert result is not None
        assert result["message_count"] == 2

    def test_excludes_tool_results(self, tmp_path: Path):
        jsonl = tmp_path / "s.jsonl"
        jsonl.write_text(
            '{"type":"user","timestamp":"2026-01-01T00:00:00Z","message":{"content":"Hi"}}\n'
            '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"x"}]}}\n'
            '{"type":"user","toolUseResult":{"type":"create"},"message":{"content":"result"}}\n'
            '{"type":"user","timestamp":"2026-01-01T00:00:01Z","message":{"content":"Bye"}}\n'
        )
        result = _scan_session_jsonl(jsonl, "P")
        assert result is not None
        assert result["message_count"] == 2

    def test_excludes_noise_messages(self, tmp_path: Path):
        jsonl = tmp_path / "s.jsonl"
        jsonl.write_text(
            '{"type":"user","timestamp":"2026-01-01T00:00:00Z","message":{"content":"<local-command-caveat>noise</local-command-caveat>"}}\n'
            '{"type":"user","message":{"content":"<command-name>/clear</command-name>"}}\n'
            '{"type":"user","timestamp":"2026-01-01T00:00:01Z","message":{"content":"real question"}}\n'
        )
        result = _scan_session_jsonl(jsonl, "P")
        assert result is not None
        assert result["message_count"] == 1


class TestProjectNameResolution:
    """Test git remote and cwd-based project name resolution."""

    def test_name_from_cwd_simple(self):
        assert _name_from_cwd("/Users/fan/work/VoiceText") == "VoiceText"

    def test_name_from_cwd_worktree(self):
        assert _name_from_cwd("/Users/fan/work/VoiceText.feat-ui-experience") == "VoiceText"

    def test_git_remote_name_normal_repo(self, tmp_path: Path):
        """Reads remote origin URL from .git/config."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n'
            "    url = git@github.com:Airead/WenZi.git\n"
            "    fetch = +refs/heads/*:refs/remotes/origin/*\n"
        )
        assert _git_remote_name(str(tmp_path)) == "WenZi"

    def test_git_remote_name_https(self, tmp_path: Path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n'
            "    url = https://github.com/Airead/WenZi.git\n"
        )
        assert _git_remote_name(str(tmp_path)) == "WenZi"

    def test_git_remote_name_worktree(self, tmp_path: Path):
        """Follows .git file to main repo config."""
        # Set up main repo
        main = tmp_path / "main"
        main.mkdir()
        git_dir = main / ".git"
        git_dir.mkdir()
        worktrees = git_dir / "worktrees" / "wt1"
        worktrees.mkdir(parents=True)
        (git_dir / "config").write_text(
            '[remote "origin"]\n'
            "    url = git@github.com:Airead/WenZi.git\n"
        )

        # Set up worktree
        wt = tmp_path / "wt1"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {worktrees}\n")

        assert _git_remote_name(str(wt)) == "WenZi"

    def test_git_remote_name_no_git(self, tmp_path: Path):
        assert _git_remote_name(str(tmp_path)) == ""

    def test_resolve_project_name_caches(self, tmp_path: Path):
        """Second call uses cache, not filesystem."""
        from cc_sessions.scanner import _project_name_cache

        cwd = str(tmp_path / "nonexistent")
        # Clear any existing cache entry
        _project_name_cache.pop(cwd, None)
        name1 = _resolve_project_name(cwd, "fallback")
        assert name1 == "nonexistent"  # from _name_from_cwd

        # Cached — even with different fallback, returns same
        name2 = _resolve_project_name(cwd, "other")
        assert name2 == "nonexistent"

        # Cleanup
        _project_name_cache.pop(cwd, None)

    def test_resolve_project_name_empty_cwd_uses_fallback(self):
        assert _resolve_project_name("", "MyFallback") == "MyFallback"

    def test_scan_jsonl_uses_cwd_for_project(self, tmp_path: Path):
        """JSONL scanner resolves project name from cwd, not dir name."""
        from cc_sessions.scanner import _project_name_cache

        jsonl = tmp_path / "s1.jsonl"
        cwd = "/Users/fan/work/VoiceText.feat-branch"
        jsonl.write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                        "cwd": cwd, "message": {"content": "Hi"}}) + "\n"
        )
        # Clear cache
        _project_name_cache.pop(cwd, None)
        result = _scan_session_jsonl(jsonl, "branch")
        assert result is not None
        assert result["project"] == "VoiceText"

        # Cleanup
        _project_name_cache.pop(cwd, None)

    def test_resolve_from_subdirectory(self, tmp_path: Path):
        """Session started from repo subdirectory resolves to repo name."""
        from cc_sessions.scanner import _project_name_cache

        # Set up git repo with remote
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n'
            "    url = git@github.com:Airead/WenZi.git\n"
        )
        sub = tmp_path / "docs"
        sub.mkdir()

        cwd = str(sub)
        _project_name_cache.pop(cwd, None)
        assert _resolve_project_name(cwd, "docs") == "WenZi"
        _project_name_cache.pop(cwd, None)

    def test_resolve_subdirectory_no_remote(self, tmp_path: Path):
        """Subdirectory of repo without remote falls back to repo dir basename."""
        from cc_sessions.scanner import _project_name_cache

        # Git repo without remote
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("[core]\n    bare = false\n")
        sub = tmp_path / "src" / "lib"
        sub.mkdir(parents=True)

        cwd = str(sub)
        _project_name_cache.pop(cwd, None)
        assert _resolve_project_name(cwd, "lib") == tmp_path.name
        _project_name_cache.pop(cwd, None)

    def test_scan_jsonl_subdirectory_resolves_to_repo(self, tmp_path: Path):
        """Integration: JSONL with subdirectory CWD resolves project from git root."""
        from cc_sessions.scanner import _project_name_cache

        # Set up git repo
        repo = tmp_path / "MyProject"
        repo.mkdir()
        git_dir = repo / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n'
            "    url = git@github.com:User/MyProject.git\n"
        )
        sub = repo / "docs"
        sub.mkdir()

        cwd = str(sub)
        jsonl = tmp_path / "s1.jsonl"
        jsonl.write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                        "cwd": cwd, "message": {"content": "Hello"}}) + "\n"
        )
        _project_name_cache.pop(cwd, None)
        result = _scan_session_jsonl(jsonl, "docs")
        assert result is not None
        assert result["project"] == "MyProject"
        _project_name_cache.pop(cwd, None)


class TestScannerClearCache:
    """Test SessionScanner.clear_cache() method."""

    def test_clear_cache_resets_all(self, tmp_path: Path):
        """clear_cache() empties disk cache, index supplements, and project name cache."""
        from cc_sessions.scanner import _project_name_cache

        base = tmp_path / "projects"
        base.mkdir()
        cache_path = tmp_path / "cache.json"
        scanner = SessionScanner(base_dir=base, cache_path=cache_path)

        scanner._cache.put("test.jsonl", 1.0, {"session_id": "t"})
        scanner._cache.save()
        scanner._index_supplements["idx"] = (1.0, {})
        _project_name_cache["test_cwd"] = "TestProject"

        scanner.clear_cache()

        assert scanner._cache.get("test.jsonl") is None
        assert not cache_path.exists()
        assert scanner._index_supplements == {}
        assert "test_cwd" not in _project_name_cache
