"""Tests for PreviewHistoryStore."""

from __future__ import annotations

from voicetext.enhance.preview_history import PreviewHistoryStore, PreviewRecord


def _make_record(**overrides) -> PreviewRecord:
    defaults = dict(
        timestamp="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T08:00:00",
        action="confirm",
        asr_text="hello",
        enhanced_text="Hello.",
        final_text="Hello.",
        enhance_mode="proofread",
        stt_model="funasr",
        llm_model="openai/gpt-4o",
        wav_data=b"\x00" * 100,
        audio_duration=2.5,
        source="voice",
    )
    defaults.update(overrides)
    return PreviewRecord(**defaults)


class TestPreviewHistoryStore:
    def test_empty_store(self):
        store = PreviewHistoryStore()
        assert store.count() == 0
        assert store.get_all() == []
        assert store.get(0) is None

    def test_add_and_get(self):
        store = PreviewHistoryStore()
        r = _make_record(asr_text="first")
        store.add(r)
        assert store.count() == 1
        assert store.get(0) is r

    def test_get_all_newest_first(self):
        store = PreviewHistoryStore()
        store.add(_make_record(asr_text="first"))
        store.add(_make_record(asr_text="second"))
        store.add(_make_record(asr_text="third"))

        items = store.get_all()
        assert len(items) == 3
        assert items[0].asr_text == "third"
        assert items[1].asr_text == "second"
        assert items[2].asr_text == "first"

    def test_get_by_index(self):
        store = PreviewHistoryStore()
        store.add(_make_record(asr_text="first"))
        store.add(_make_record(asr_text="second"))

        assert store.get(0).asr_text == "second"  # newest
        assert store.get(1).asr_text == "first"
        assert store.get(2) is None

    def test_max_size_eviction(self):
        store = PreviewHistoryStore(max_size=3)
        for i in range(5):
            store.add(_make_record(asr_text=f"text{i}"))

        assert store.count() == 3
        items = store.get_all()
        assert items[0].asr_text == "text4"
        assert items[1].asr_text == "text3"
        assert items[2].asr_text == "text2"

    def test_clear(self):
        store = PreviewHistoryStore()
        store.add(_make_record())
        store.add(_make_record())
        store.clear()
        assert store.count() == 0

    def test_cancel_record_has_none_timestamp(self):
        store = PreviewHistoryStore()
        store.add(_make_record(timestamp=None))
        assert store.get(0).timestamp is None

    def test_update_timestamp(self):
        store = PreviewHistoryStore()
        store.add(_make_record(timestamp=None, asr_text="first"))
        store.add(_make_record(timestamp=None, asr_text="second"))

        # Index 0 = newest ("second")
        store.update_timestamp(0, "2026-01-01T12:00:00+00:00")
        assert store.get(0).timestamp == "2026-01-01T12:00:00+00:00"
        assert store.get(1).timestamp is None

    def test_update_timestamp_out_of_range(self):
        store = PreviewHistoryStore()
        store.add(_make_record(timestamp=None))
        # Should not raise
        store.update_timestamp(5, "ts")
        assert store.get(0).timestamp is None

    def test_move_to_front(self):
        store = PreviewHistoryStore()
        store.add(_make_record(asr_text="old"))
        store.add(_make_record(asr_text="mid"))
        store.add(_make_record(asr_text="new"))

        # index 2 = oldest ("old"), move it to front
        store.move_to_front(2)
        assert store.get(0).asr_text == "old"
        assert store.get(1).asr_text == "new"
        assert store.get(2).asr_text == "mid"

    def test_move_to_front_index_zero_is_noop(self):
        store = PreviewHistoryStore()
        store.add(_make_record(asr_text="a"))
        store.add(_make_record(asr_text="b"))

        store.move_to_front(0)
        assert store.get(0).asr_text == "b"
        assert store.get(1).asr_text == "a"

    def test_move_to_front_out_of_range(self):
        store = PreviewHistoryStore()
        store.add(_make_record(asr_text="only"))
        # Should not raise
        store.move_to_front(5)
        assert store.count() == 1

    def test_move_to_front_prevents_eviction(self):
        store = PreviewHistoryStore(max_size=3)
        store.add(_make_record(asr_text="keep_me"))
        store.add(_make_record(asr_text="b"))
        store.add(_make_record(asr_text="c"))

        # "keep_me" is oldest (index 2), move to front
        store.move_to_front(2)
        # Now add a new record — "b" should be evicted, not "keep_me"
        store.add(_make_record(asr_text="d"))
        texts = [store.get(i).asr_text for i in range(store.count())]
        assert "keep_me" in texts
        assert "b" not in texts

    def test_wav_data_stored(self):
        store = PreviewHistoryStore()
        wav = b"RIFF" + b"\x00" * 1000
        store.add(_make_record(wav_data=wav))
        assert store.get(0).wav_data is wav

    def test_clipboard_source(self):
        store = PreviewHistoryStore()
        store.add(_make_record(source="clipboard", wav_data=None))
        assert store.get(0).source == "clipboard"
        assert store.get(0).wav_data is None

    def test_default_max_size(self):
        store = PreviewHistoryStore()
        assert store._max_size == 10
