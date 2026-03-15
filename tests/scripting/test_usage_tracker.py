"""Tests for the usage frequency tracker."""

import os
import tempfile

from voicetext.scripting.sources.usage_tracker import UsageTracker


class TestUsageTracker:
    def _make_tracker(self):
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "usage.json")
        return UsageTracker(path=path), path

    def test_empty_score(self):
        tracker, _ = self._make_tracker()
        assert tracker.score("saf", "app:Safari") == 0

    def test_record_and_score(self):
        tracker, _ = self._make_tracker()
        tracker.record("saf", "app:Safari")
        assert tracker.score("saf", "app:Safari") == 1
        tracker.record("saf", "app:Safari")
        assert tracker.score("saf", "app:Safari") == 2

    def test_different_queries(self):
        tracker, _ = self._make_tracker()
        tracker.record("saf", "app:Safari")
        tracker.record("chr", "app:Chrome")
        assert tracker.score("saf", "app:Safari") == 1
        assert tracker.score("chr", "app:Chrome") == 1
        assert tracker.score("saf", "app:Chrome") == 0

    def test_prefix_grouping(self):
        """Queries are grouped by first 3 characters."""
        tracker, _ = self._make_tracker()
        tracker.record("safari", "app:Safari")
        # "saf" prefix should match
        assert tracker.score("saf", "app:Safari") == 1

    def test_persistence(self):
        tracker, path = self._make_tracker()
        tracker.record("saf", "app:Safari")
        # Create new tracker reading the same file
        tracker2 = UsageTracker(path=path)
        assert tracker2.score("saf", "app:Safari") == 1

    def test_empty_query_ignored(self):
        tracker, _ = self._make_tracker()
        tracker.record("", "app:Safari")
        assert tracker.score("", "app:Safari") == 0

    def test_empty_item_id_ignored(self):
        tracker, _ = self._make_tracker()
        tracker.record("saf", "")
        assert tracker.score("saf", "") == 0

    def test_clear(self):
        tracker, _ = self._make_tracker()
        tracker.record("saf", "app:Safari")
        assert tracker.score("saf", "app:Safari") == 1
        tracker.clear()
        assert tracker.score("saf", "app:Safari") == 0

    def test_nonexistent_file(self):
        tracker = UsageTracker(path="/tmp/nonexistent_usage.json")
        assert tracker.score("saf", "app:Safari") == 0

    def test_corrupt_file_handled(self):
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "usage.json")
        with open(path, "w") as f:
            f.write("not json")
        tracker = UsageTracker(path=path)
        # Should not crash, just return 0
        assert tracker.score("saf", "app:Safari") == 0
