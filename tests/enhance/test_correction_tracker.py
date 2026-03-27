"""Tests for CorrectionTracker: SQLite schema, diff extraction, and record logic."""

import json
import sqlite3

from wenzi.enhance.correction_tracker import CorrectionTracker, extract_word_pairs


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------


def test_init_creates_parent_directory(tmp_path):
    """CorrectionTracker should create missing parent dirs (new-user scenario)."""
    db_path = str(tmp_path / "nonexistent" / "subdir" / "tracker.db")
    tracker = CorrectionTracker(db_path=db_path)
    assert tracker.is_empty() is True


def test_init_creates_tables(tmp_path):
    db_path = str(tmp_path / "tracker.db")
    CorrectionTracker(db_path=db_path)
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "correction_sessions" in tables
    assert "correction_pairs" in tables
    conn.close()


def test_is_empty_true_on_fresh_db(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "tracker.db"))
    assert tracker.is_empty() is True


def test_is_empty_false_after_record(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "tracker.db"))
    tracker.record(asr_text="hello", enhanced_text="", final_text="hello",
        asr_model="FunASR", llm_model="", app_bundle_id="",
        enhance_mode="proofread", audio_duration=None, user_corrected=False)
    assert tracker.is_empty() is False


def test_init_sets_schema_version(tmp_path):
    db_path = str(tmp_path / "tracker.db")
    CorrectionTracker(db_path=db_path)
    conn = sqlite3.connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 1
    conn.close()


def test_init_enables_foreign_keys(tmp_path):
    db_path = str(tmp_path / "tracker.db")
    tracker = CorrectionTracker(db_path=db_path)
    conn = tracker._get_conn()
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


# ---------------------------------------------------------------------------
# extract_word_pairs
# ---------------------------------------------------------------------------


def test_extract_simple_replace():
    pairs = extract_word_pairs("我在用cloud做开发", "我在用claude做开发")
    assert ("cloud", "claude") in pairs


def test_extract_cjk_grouped():
    pairs = extract_word_pairs("我在用库伯尼特斯做编排", "我在用Kubernetes做编排")
    assert ("库伯尼特斯", "Kubernetes") in pairs


def test_extract_latin_space_restored():
    pairs = extract_word_pairs("use boys test app", "use VoiceText app")
    assert any("VoiceText" in p[1] for p in pairs)


def test_extract_skip_large_replace():
    a = "一二三四五六七八九十壹贰"
    b = "ABCDEFGHIJKLMN"
    pairs = extract_word_pairs(a, b, max_replace_tokens=8)
    assert len(pairs) == 0


def test_extract_identical_texts():
    pairs = extract_word_pairs("hello world", "hello world")
    assert pairs == []


# ---------------------------------------------------------------------------
# record() method
# ---------------------------------------------------------------------------


def test_record_creates_session(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    tracker.record(asr_text="我在用cloud做开发", enhanced_text="我在用claude做开发",
        final_text="我在用claude做开发", asr_model="FunASR", llm_model="gpt-4o",
        app_bundle_id="com.apple.Terminal", enhance_mode="proofread",
        audio_duration=2.0, user_corrected=False)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    assert conn.execute("SELECT COUNT(*) FROM correction_sessions").fetchone()[0] == 1
    conn.close()


def test_record_creates_asr_pairs(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    tracker.record(asr_text="我在用cloud做开发", enhanced_text="我在用claude做开发",
        final_text="我在用claude做开发", asr_model="FunASR", llm_model="gpt-4o",
        app_bundle_id="", enhance_mode="proofread", audio_duration=None, user_corrected=False)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    pairs = conn.execute("SELECT source, original_word, corrected_word FROM correction_pairs").fetchall()
    conn.close()
    asr_pairs = [(o, c) for s, o, c in pairs if s == "asr"]
    assert ("cloud", "claude") in asr_pairs


def test_record_no_llm_pairs_when_not_user_corrected(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    tracker.record(asr_text="我在用cloud做开发", enhanced_text="我在用claude做开发",
        final_text="我在用claude做开发", asr_model="FunASR", llm_model="gpt-4o",
        app_bundle_id="", enhance_mode="proofread", audio_duration=None, user_corrected=False)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    assert conn.execute("SELECT COUNT(*) FROM correction_pairs WHERE source='llm'").fetchone()[0] == 0
    conn.close()


def test_record_creates_llm_pairs_when_user_corrected(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    tracker.record(asr_text="我在用cloud做开发", enhanced_text="我在用cloud做开发",
        final_text="我在用claude做开发", asr_model="FunASR", llm_model="gpt-4o",
        app_bundle_id="", enhance_mode="proofread", audio_duration=None, user_corrected=True)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    llm_pairs = conn.execute("SELECT original_word, corrected_word FROM correction_pairs WHERE source='llm'").fetchall()
    conn.close()
    assert ("cloud", "claude") in llm_pairs


def test_record_upsert_increments_count(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    for _ in range(3):
        tracker.record(asr_text="我在用cloud做开发", enhanced_text="我在用claude做开发",
            final_text="我在用claude做开发", asr_model="FunASR", llm_model="gpt-4o",
            app_bundle_id="", enhance_mode="proofread", audio_duration=None, user_corrected=False)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    count = conn.execute("SELECT count FROM correction_pairs WHERE corrected_word='claude' AND source='asr'").fetchone()[0]
    conn.close()
    assert count == 3


def test_record_no_pairs_when_texts_identical(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    tracker.record(asr_text="hello", enhanced_text="hello", final_text="hello",
        asr_model="FunASR", llm_model="gpt-4o", app_bundle_id="",
        enhance_mode="proofread", audio_duration=None, user_corrected=False)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    assert conn.execute("SELECT COUNT(*) FROM correction_pairs").fetchone()[0] == 0
    conn.close()


# ---------------------------------------------------------------------------
# Task 4: Auto-exclusion filters
# ---------------------------------------------------------------------------


def test_mark_excluded_manual(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    tracker.record(asr_text="我在用cloud做开发", enhanced_text="",
        final_text="我在用claude做开发", asr_model="FunASR", llm_model="",
        app_bundle_id="", enhance_mode="proofread", audio_duration=None, user_corrected=False)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    pair_id = conn.execute("SELECT id FROM correction_pairs LIMIT 1").fetchone()[0]
    conn.close()
    tracker.mark_excluded(pair_id, excluded=True)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    excluded = conn.execute("SELECT excluded FROM correction_pairs WHERE id=?", (pair_id,)).fetchone()[0]
    conn.close()
    assert excluded == 1


def test_mark_excluded_unmark(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    tracker.record(asr_text="我在用cloud做开发", enhanced_text="",
        final_text="我在用claude做开发", asr_model="FunASR", llm_model="",
        app_bundle_id="", enhance_mode="proofread", audio_duration=None, user_corrected=False)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    pair_id = conn.execute("SELECT id FROM correction_pairs LIMIT 1").fetchone()[0]
    conn.close()
    tracker.mark_excluded(pair_id, excluded=True)
    tracker.mark_excluded(pair_id, excluded=False)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    excluded = conn.execute("SELECT excluded FROM correction_pairs WHERE id=?", (pair_id,)).fetchone()[0]
    conn.close()
    assert excluded == 0


def test_short_latin_original_auto_excluded(tmp_path):
    """Short Latin original_word (< 4 chars) gets excluded=1."""
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    tracker.record(asr_text="use set for config", enhanced_text="",
        final_text="use STT for config", asr_model="FunASR", llm_model="",
        app_bundle_id="", enhance_mode="proofread", audio_duration=None, user_corrected=False)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    row = conn.execute("SELECT excluded FROM correction_pairs WHERE original_word='set' AND corrected_word='STT'").fetchone()
    conn.close()
    assert row is not None, "Expected a correction pair for set→STT"
    assert row[0] == 1


def test_normal_word_not_excluded(tmp_path):
    """Technical corrections (e.g. cloud -> Kubernetes) should not be auto-excluded."""
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    tracker.record(asr_text="我在用cloud做开发", enhanced_text="",
        final_text="我在用Kubernetes做开发", asr_model="FunASR", llm_model="",
        app_bundle_id="", enhance_mode="proofread", audio_duration=None, user_corrected=False)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    row = conn.execute("SELECT excluded FROM correction_pairs WHERE corrected_word='Kubernetes'").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 0


# ---------------------------------------------------------------------------
# Task 5: Injection queries
# ---------------------------------------------------------------------------


def _make_tracker_with_data(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    for _ in range(5):
        tracker.record(asr_text="我在用cloud做开发", enhanced_text="我在用Kubernetes做开发",
            final_text="我在用Kubernetes做开发", asr_model="FunASR", llm_model="gpt-4o",
            app_bundle_id="com.apple.Terminal", enhance_mode="proofread",
            audio_duration=None, user_corrected=False)
    return tracker


def test_get_asr_hotwords_above_threshold(tmp_path):
    tracker = _make_tracker_with_data(tmp_path)
    hotwords = tracker.get_asr_hotwords(asr_model="FunASR", app_bundle_id="com.apple.Terminal", min_count=5)
    assert "Kubernetes" in hotwords


def test_get_asr_hotwords_below_threshold(tmp_path):
    tracker = _make_tracker_with_data(tmp_path)
    hotwords = tracker.get_asr_hotwords(asr_model="FunASR", app_bundle_id="com.apple.Terminal", min_count=10)
    assert "Kubernetes" not in hotwords


def test_get_asr_hotwords_wrong_model(tmp_path):
    tracker = _make_tracker_with_data(tmp_path)
    hotwords = tracker.get_asr_hotwords(asr_model="MLX Whisper", app_bundle_id="com.apple.Terminal", min_count=5)
    assert "Kubernetes" not in hotwords


def test_get_asr_hotwords_respects_top_k(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    # Use unique non-common-word targets so they are not excluded
    words = [("cloudA", "KubernetesA"), ("cloudB", "KubernetesB"), ("cloudC", "KubernetesC")]
    for orig, corr in words:
        for _ in range(5):
            tracker.record(asr_text=f"use {orig} now", enhanced_text="", final_text=f"use {corr} now",
                asr_model="FunASR", llm_model="", app_bundle_id="",
                enhance_mode="proofread", audio_duration=None, user_corrected=False)
    hotwords = tracker.get_asr_hotwords(asr_model="FunASR", app_bundle_id="", min_count=5, top_k=2)
    assert len(hotwords) <= 2


def test_get_llm_vocab_with_user_corrections(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    for _ in range(5):
        tracker.record(asr_text="我在用cloud做开发", enhanced_text="我在用cloud做开发",
            final_text="我在用Kubernetes做开发", asr_model="FunASR", llm_model="gpt-4o",
            app_bundle_id="com.apple.Terminal", enhance_mode="proofread",
            audio_duration=None, user_corrected=True)
    vocab = tracker.get_llm_vocab(llm_model="gpt-4o", app_bundle_id="com.apple.Terminal", min_count=5)
    words = [v["corrected_word"] for v in vocab]
    assert "Kubernetes" in words


def test_get_llm_vocab_includes_variants(tmp_path):
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    for _ in range(5):
        tracker.record(asr_text="我在用cloud做开发", enhanced_text="我在用cloud做开发",
            final_text="我在用Kubernetes做开发", asr_model="FunASR", llm_model="gpt-4o",
            app_bundle_id="", enhance_mode="proofread", audio_duration=None, user_corrected=True)
    vocab = tracker.get_llm_vocab(llm_model="gpt-4o", app_bundle_id="", min_count=5)
    entry = next(v for v in vocab if v["corrected_word"] == "Kubernetes")
    assert "cloud" in entry["variants"]


# ---------------------------------------------------------------------------
# rebuild_from_history()
# ---------------------------------------------------------------------------


def _write_jsonl(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_rebuild_processes_tracked_records(tmp_path):
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    _write_jsonl(history_dir / "conversation_history.jsonl", [{
        "timestamp": "2026-03-01T10:00:00+00:00",
        "asr_text": "我在用cloud做开发",
        "enhanced_text": "我在用claude做开发",
        "final_text": "我在用claude做开发",
        "enhance_mode": "proofread",
        "stt_model": "FunASR",
        "llm_model": "gpt-4o",
        "user_corrected": False,
        "audio_duration": 2.0,
        "preview_enabled": True,
        "correction_tracked": True,
    }])
    from wenzi.enhance.conversation_history import ConversationHistory
    ch = ConversationHistory(data_dir=str(history_dir))
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    count = tracker.rebuild_from_history(ch)
    assert count == 1
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    assert conn.execute("SELECT COUNT(*) FROM correction_sessions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM correction_pairs").fetchone()[0] > 0
    conn.close()


def test_rebuild_skips_untracked_records(tmp_path):
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    _write_jsonl(history_dir / "conversation_history.jsonl", [{
        "timestamp": "2026-03-01T10:00:00+00:00",
        "asr_text": "hello", "enhanced_text": "Hello World",
        "final_text": "Hello World", "enhance_mode": "translate_en",
        "stt_model": "FunASR", "llm_model": "gpt-4o",
        "user_corrected": False, "preview_enabled": True,
        "correction_tracked": False,
    }])
    from wenzi.enhance.conversation_history import ConversationHistory
    ch = ConversationHistory(data_dir=str(history_dir))
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    assert tracker.rebuild_from_history(ch) == 0


def test_rebuild_clears_existing_data(tmp_path):
    """Rebuild clears old data before importing."""
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    _write_jsonl(history_dir / "conversation_history.jsonl", [{
        "timestamp": "2026-03-01T10:00:00+00:00",
        "asr_text": "我在用cloud做开发",
        "enhanced_text": "我在用claude做开发",
        "final_text": "我在用claude做开发",
        "enhance_mode": "proofread",
        "stt_model": "FunASR", "llm_model": "gpt-4o",
        "user_corrected": False, "preview_enabled": True,
        "correction_tracked": True,
    }])
    from wenzi.enhance.conversation_history import ConversationHistory
    ch = ConversationHistory(data_dir=str(history_dir))
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    tracker.rebuild_from_history(ch)
    # Rebuild again — should get same count (not doubled)
    count2 = tracker.rebuild_from_history(ch)
    assert count2 == 1
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    assert conn.execute("SELECT COUNT(*) FROM correction_sessions").fetchone()[0] == 1
    conn.close()


def test_rebuild_stores_input_context(tmp_path):
    """Rebuild preserves input_context as JSON in correction_sessions."""
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    _write_jsonl(history_dir / "conversation_history.jsonl", [{
        "timestamp": "2026-03-01T10:00:00+00:00",
        "asr_text": "我在用cloud做开发",
        "enhanced_text": "我在用claude做开发",
        "final_text": "我在用claude做开发",
        "enhance_mode": "proofread",
        "stt_model": "FunASR", "llm_model": "gpt-4o",
        "user_corrected": False, "preview_enabled": True,
        "correction_tracked": True,
        "input_context": {"app_name": "Terminal", "bundle_id": "com.apple.Terminal"},
    }])
    from wenzi.enhance.conversation_history import ConversationHistory
    ch = ConversationHistory(data_dir=str(history_dir))
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    tracker.rebuild_from_history(ch)
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    row = conn.execute("SELECT input_context, app_bundle_id FROM correction_sessions").fetchone()
    conn.close()
    assert '"Terminal"' in row[0]
    assert row[1] == "com.apple.Terminal"


# ---------------------------------------------------------------------------
# backfill_from_history()
# ---------------------------------------------------------------------------


def test_backfill_skips_existing_records(tmp_path):
    """Backfill does not duplicate records already in the database."""
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    _write_jsonl(history_dir / "conversation_history.jsonl", [{
        "timestamp": "2026-03-01T10:00:00+00:00",
        "asr_text": "我在用cloud做开发",
        "enhanced_text": "我在用claude做开发",
        "final_text": "我在用claude做开发",
        "enhance_mode": "proofread",
        "stt_model": "FunASR", "llm_model": "gpt-4o",
        "user_corrected": False, "preview_enabled": True,
        "correction_tracked": True,
    }])
    from wenzi.enhance.conversation_history import ConversationHistory
    ch = ConversationHistory(data_dir=str(history_dir))
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))
    count1 = tracker.backfill_from_history(ch)
    assert count1 == 1
    count2 = tracker.backfill_from_history(ch)
    assert count2 == 0
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    assert conn.execute("SELECT COUNT(*) FROM correction_sessions").fetchone()[0] == 1
    conn.close()


def test_backfill_preserves_existing_data(tmp_path):
    """Backfill adds new records without affecting existing ones."""
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    tracker = CorrectionTracker(db_path=str(tmp_path / "t.db"))

    # Record a live session first
    tracker.record(
        asr_text="live test", enhanced_text="", final_text="live result",
        asr_model="FunASR", llm_model="", app_bundle_id="",
        enhance_mode="proofread", audio_duration=None, user_corrected=False,
    )

    # Now backfill from history
    _write_jsonl(history_dir / "conversation_history.jsonl", [{
        "timestamp": "2026-03-01T10:00:00+00:00",
        "asr_text": "我在用cloud做开发",
        "enhanced_text": "我在用claude做开发",
        "final_text": "我在用claude做开发",
        "enhance_mode": "proofread",
        "stt_model": "FunASR", "llm_model": "gpt-4o",
        "user_corrected": False, "preview_enabled": True,
        "correction_tracked": True,
    }])
    from wenzi.enhance.conversation_history import ConversationHistory
    ch = ConversationHistory(data_dir=str(history_dir))
    count = tracker.backfill_from_history(ch)
    assert count == 1

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    assert conn.execute("SELECT COUNT(*) FROM correction_sessions").fetchone()[0] == 2
    conn.close()
