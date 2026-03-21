"""Tests for the cc-sessions launcher source search logic."""

from datetime import datetime, timezone, timedelta


def _make_sessions():
    return [
        {
            "session_id": "s1",
            "file_path": "/tmp/s1.jsonl",
            "project": "VoiceText",
            "cwd": "/Users/test/work/VoiceText",
            "title": "refactor config to dataclass",
            "first_prompt": "refactor config to dataclass",
            "git_branch": "main",
            "created": "2026-03-21T10:00:00Z",
            "modified": "2026-03-21T12:00:00Z",
            "message_count": 42,
            "version": "2.1.81",
        },
        {
            "session_id": "s2",
            "file_path": "/tmp/s2.jsonl",
            "project": "VoiceText",
            "cwd": "/Users/test/work/VoiceText",
            "title": "fix dark mode in settings",
            "first_prompt": "fix dark mode in settings",
            "git_branch": "feat-dark",
            "created": "2026-03-20T10:00:00Z",
            "modified": "2026-03-20T11:00:00Z",
            "message_count": 15,
            "version": "2.1.80",
        },
        {
            "session_id": "s3",
            "file_path": "/tmp/s3.jsonl",
            "project": "btc-blocks-monitor",
            "cwd": "/Users/test/work/btc-blocks-monitor",
            "title": "add pool detection for Foundry",
            "first_prompt": "add pool detection for Foundry",
            "git_branch": "main",
            "created": "2026-03-19T10:00:00Z",
            "modified": "2026-03-19T11:00:00Z",
            "message_count": 8,
            "version": "2.1.79",
        },
    ]


class TestParseQuery:
    def test_plain_query(self):
        from cc_sessions.init_plugin import _parse_query

        project, query = _parse_query("refactor config")
        assert project is None
        assert query == "refactor config"

    def test_project_filter(self):
        from cc_sessions.init_plugin import _parse_query

        project, query = _parse_query("@voice")
        assert project == "voice"
        assert query == ""

    def test_project_filter_with_query(self):
        from cc_sessions.init_plugin import _parse_query

        project, query = _parse_query("@btc pool detection")
        assert project == "btc"
        assert query == "pool detection"

    def test_empty_query(self):
        from cc_sessions.init_plugin import _parse_query

        project, query = _parse_query("")
        assert project is None
        assert query == ""


class TestFilterSessions:
    def test_no_filter_returns_all(self):
        from cc_sessions.init_plugin import _filter_sessions

        result = _filter_sessions(_make_sessions(), project_filter=None, query="")
        assert len(result) == 3

    def test_project_filter(self):
        from cc_sessions.init_plugin import _filter_sessions

        result = _filter_sessions(
            _make_sessions(), project_filter="voice", query=""
        )
        assert len(result) == 2
        assert all(s["project"] == "VoiceText" for s in result)

    def test_project_filter_fuzzy(self):
        from cc_sessions.init_plugin import _filter_sessions

        result = _filter_sessions(
            _make_sessions(), project_filter="btc", query=""
        )
        assert len(result) == 1
        assert result[0]["project"] == "btc-blocks-monitor"

    def test_query_filter(self):
        from cc_sessions.init_plugin import _filter_sessions

        result = _filter_sessions(
            _make_sessions(), project_filter=None, query="dark mode"
        )
        assert len(result) == 1
        assert result[0]["session_id"] == "s2"

    def test_project_and_query_combined(self):
        from cc_sessions.init_plugin import _filter_sessions

        result = _filter_sessions(
            _make_sessions(), project_filter="voice", query="config"
        )
        assert len(result) == 1
        assert result[0]["session_id"] == "s1"


class TestTimeAgo:
    def test_just_now(self):
        from cc_sessions.init_plugin import _time_ago

        now = datetime.now(timezone.utc)
        ts = (now - timedelta(seconds=30)).isoformat()
        assert _time_ago(ts) == "just now"

    def test_minutes_ago(self):
        from cc_sessions.init_plugin import _time_ago

        now = datetime.now(timezone.utc)
        ts = (now - timedelta(minutes=5)).isoformat()
        assert _time_ago(ts) == "5 min ago"

    def test_hours_ago(self):
        from cc_sessions.init_plugin import _time_ago

        now = datetime.now(timezone.utc)
        ts = (now - timedelta(hours=3)).isoformat()
        assert "3 hour" in _time_ago(ts)

    def test_days_ago(self):
        from cc_sessions.init_plugin import _time_ago

        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=5)).isoformat()
        assert "5 day" in _time_ago(ts)

    def test_invalid_timestamp(self):
        from cc_sessions.init_plugin import _time_ago

        assert _time_ago("not-a-date") == ""
        assert _time_ago("") == ""
