"""Tests for CorrectionTracker: SQLite schema, diff extraction, and record logic."""

import sqlite3

from wenzi.enhance.correction_tracker import CorrectionTracker, extract_word_pairs


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------


def test_init_creates_tables(tmp_path):
    db_path = str(tmp_path / "tracker.db")
    tracker = CorrectionTracker(db_path=db_path)
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "correction_sessions" in tables
    assert "correction_pairs" in tables
    conn.close()


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
