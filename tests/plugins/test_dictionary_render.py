"""Tests for dictionary plugin HTML renderer."""


SAMPLE_DATA = {
    "ec": {
        "exam_type": ["CET4", "CET6"],
        "word": {
            "usphone": "həˈloʊ",
            "ukphone": "həˈləʊ",
            "trs": [
                {"pos": "int.", "tran": "喂，你好"},
                {"pos": "n.", "tran": "招呼，问候"},
            ],
            "wfs": [
                {"wf": {"name": "复数", "value": "hellos"}},
                {"wf": {"name": "过去式", "value": "helloed"}},
            ],
            "return-phrase": "hello",
        },
    },
    "simple": {
        "word": [{"usphone": "həˈloʊ", "ukphone": "həˈləʊ"}],
    },
    "phrs": {
        "phrs": [
            {"headword": "say hello", "translation": "打招呼"},
            {"headword": "hello world", "translation": "你好世界"},
        ],
    },
    "syno": {
        "synos": [{"pos": "int.", "ws": ["hallo", "hi"], "tran": "喂"}],
    },
    "blng_sents_part": {
        "sentence-pair": [
            {
                "sentence": "Hello, how are you?",
                "sentence-translation": "你好，你怎么样？",
            },
        ],
    },
    "collins": {
        "collins_entries": [
            {
                "entries": {
                    "entry": [
                        {
                            "tran_entry": [
                                {
                                    "pos_entry": {
                                        "pos": "CONVENTION",
                                        "pos_tips": "习惯表达",
                                    },
                                    "tran": "You say \"hello\" to someone when you meet them. 你好",
                                    "exam_sents": {
                                        "sent": [
                                            {
                                                "eng_sent": "Hello, Trish.",
                                                "chn_sent": "你好，特里斯。",
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    ]
                }
            }
        ]
    },
    "etym": {
        "etyms": {
            "zh": [{"word": "hello", "value": "感叹词，打招呼用语", "desc": "喂"}],
        },
    },
}


class TestRenderDefinition:
    def test_contains_phonetics(self):
        from dictionary.render import render_definition

        html = render_definition(SAMPLE_DATA, "hello")
        assert "həˈloʊ" in html
        assert "həˈləʊ" in html

    def test_contains_audio_buttons(self):
        from dictionary.render import render_definition

        html = render_definition(SAMPLE_DATA, "hello")
        assert "dictvoice?audio=hello&type=2" in html  # US
        assert "dictvoice?audio=hello&type=1" in html  # UK
        assert "audio-btn" in html

    def test_contains_definitions(self):
        from dictionary.render import render_definition

        html = render_definition(SAMPLE_DATA, "hello")
        assert "int." in html
        assert "喂，你好" in html
        assert "n." in html
        assert "招呼，问候" in html

    def test_contains_exam_tags(self):
        from dictionary.render import render_definition

        html = render_definition(SAMPLE_DATA, "hello")
        assert "CET4" in html
        assert "CET6" in html

    def test_contains_word_forms(self):
        from dictionary.render import render_definition

        html = render_definition(SAMPLE_DATA, "hello")
        assert "hellos" in html
        assert "helloed" in html

    def test_contains_phrases(self):
        from dictionary.render import render_definition

        html = render_definition(SAMPLE_DATA, "hello")
        assert "say hello" in html
        assert "打招呼" in html

    def test_contains_synonyms(self):
        from dictionary.render import render_definition

        html = render_definition(SAMPLE_DATA, "hello")
        assert "hallo" in html
        assert "hi" in html

    def test_contains_examples(self):
        from dictionary.render import render_definition

        html = render_definition(SAMPLE_DATA, "hello")
        assert "Hello, how are you?" in html
        assert "你好，你怎么样？" in html

    def test_contains_collins(self):
        from dictionary.render import render_definition

        html = render_definition(SAMPLE_DATA, "hello")
        assert "CONVENTION" in html
        assert "Hello, Trish." in html

    def test_contains_etymology(self):
        from dictionary.render import render_definition

        html = render_definition(SAMPLE_DATA, "hello")
        assert "感叹词，打招呼用语" in html

    def test_empty_data_returns_fallback(self):
        from dictionary.render import render_definition

        html = render_definition({}, "unknown")
        assert "unknown" in html
        assert "No definition found" in html

    def test_web_trans_fallback_for_zh2en(self):
        from dictionary.render import render_definition

        zh_data = {
            "web_trans": {
                "web-translation": [
                    {
                        "key": "你好",
                        "trans": [{"value": "Hello"}, {"value": "Hi"}],
                    },
                ],
            },
            "blng_sents_part": {
                "sentence-pair": [
                    {
                        "sentence": "Hello!",
                        "sentence-translation": "你好！",
                    },
                ],
            },
        }
        html = render_definition(zh_data, "你好")
        assert "Hello" in html
        assert "Hi" in html
        assert "No definition found" not in html

    def test_missing_sections_gracefully_skipped(self):
        from dictionary.render import render_definition

        partial = {"ec": SAMPLE_DATA["ec"]}
        html = render_definition(partial, "hello")
        assert "int." in html
        # No crash — missing sections just omitted

    def test_has_style_block(self):
        from dictionary.render import render_definition

        html = render_definition(SAMPLE_DATA, "hello")
        assert "<style>" in html
        assert "var(--text)" in html

    def test_collins_tran_strips_script_tags(self):
        from dictionary.render import render_definition

        data = {
            "collins": {
                "collins_entries": [
                    {
                        "entries": {
                            "entry": [
                                {
                                    "tran_entry": [
                                        {
                                            "pos_entry": {"pos": "N", "pos_tips": ""},
                                            "tran": 'Hello <script>alert("xss")</script>world',
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
        }
        html = render_definition(data, "test")
        assert "<script>" not in html
        assert "</script>" not in html
        # The text content between tags is kept, only the tags are stripped
        assert "Hello" in html
        assert "world" in html

    def test_collins_tran_preserves_b_tags(self):
        from dictionary.render import render_definition

        data = {
            "collins": {
                "collins_entries": [
                    {
                        "entries": {
                            "entry": [
                                {
                                    "tran_entry": [
                                        {
                                            "pos_entry": {"pos": "V", "pos_tips": ""},
                                            "tran": "You say <b>hello</b> to greet.",
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
        }
        html = render_definition(data, "test")
        assert "<b>hello</b>" in html

    def test_collins_tran_strips_event_handler_attributes(self):
        from dictionary.render import render_definition

        data = {
            "collins": {
                "collins_entries": [
                    {
                        "entries": {
                            "entry": [
                                {
                                    "tran_entry": [
                                        {
                                            "pos_entry": {"pos": "N", "pos_tips": ""},
                                            "tran": 'Hello <span onclick="alert(1)">click</span> world',
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
        }
        html = render_definition(data, "test")
        assert "onclick" not in html
        # The <span onclick=...> tag is stripped but text content remains
        assert "click" in html
        assert "Hello" in html
        assert "world" in html
