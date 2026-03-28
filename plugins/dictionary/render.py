"""HTML renderer for dictionary preview panel."""

from __future__ import annotations

import re
from html import escape
from urllib.parse import quote as _urlquote

_SAFE_TAGS_RE = re.compile(r"<(?!/?(?:b|i|em|strong)\b)[^>]+>", re.IGNORECASE)


def _sanitize_html(html: str) -> str:
    """Allow only <b>, <i>, <em>, <strong> tags, strip all others."""
    return _SAFE_TAGS_RE.sub("", html)

_STYLE = """\
<style>
  .dict-root { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
               color: var(--text); font-size: 13px; line-height: 1.5; }
  .dict-root .word { font-size: 22px; font-weight: 700; margin-bottom: 2px; }
  .dict-root .phonetic { color: var(--secondary); font-size: 13px; margin-left: 8px; }
  .dict-root .audio-btn { background: none; border: none; cursor: pointer;
                          font-size: 14px; padding: 0 2px; vertical-align: middle;
                          opacity: 0.6; }
  .dict-root .audio-btn:hover { opacity: 1.0; }
  .dict-root .exam-tags { margin: 4px 0 8px; }
  .dict-root .tag { display: inline-block; font-size: 11px; padding: 1px 6px;
                    border-radius: 3px; margin-right: 4px;
                    background: var(--border); color: var(--secondary); }
  .dict-root .section { margin-top: 12px; }
  .dict-root .section-title { font-weight: 600; font-size: 13px; color: var(--text);
                              border-bottom: 1px solid var(--border);
                              padding-bottom: 3px; margin-bottom: 6px; }
  .dict-root .pos { font-weight: 600; color: var(--secondary); margin-right: 6px; }
  .dict-root .tran { margin: 3px 0; }
  .dict-root .wf-tags { margin: 4px 0; }
  .dict-root .wf { display: inline-block; font-size: 12px; color: var(--secondary);
                   margin-right: 10px; }
  .dict-root .wf-val { color: var(--text); }
  .dict-root .phrase { margin: 3px 0; }
  .dict-root .phrase-word { font-weight: 500; }
  .dict-root .phrase-tran { color: var(--secondary); margin-left: 8px; }
  .dict-root .syno-words { color: var(--text); }
  .dict-root .example { margin: 6px 0; }
  .dict-root .example-en { font-weight: 500; }
  .dict-root .example-zh { color: var(--secondary); }
  .dict-root .collins-entry { margin: 6px 0; }
  .dict-root .collins-pos { font-weight: 600; color: var(--secondary); }
  .dict-root .etym { color: var(--secondary); font-size: 12px; font-style: italic; }
  .dict-root .fallback { color: var(--secondary); text-align: center; padding: 40px 0; }
</style>
"""


def render_definition(data: dict, word: str) -> str:
    """Render a Youdao lookup response as HTML for the preview panel."""
    parts = [_STYLE, '<div class="dict-root">']

    ec = data.get("ec", {})
    ec_word = ec.get("word", {})
    simple = data.get("simple", {})
    simple_words = simple.get("word", [{}])
    phone_data = simple_words[0] if simple_words else ec_word

    # --- Header: word + phonetics ---
    parts.append(f'<div class="word">{escape(word)}')
    usphone = phone_data.get("usphone", "")
    ukphone = phone_data.get("ukphone", "")
    audio_word = _urlquote(word)
    if usphone:
        parts.append(
            f'<span class="phonetic">US /{escape(usphone)}/</span>'
            f'<button class="audio-btn" onclick='
            f""""new Audio('https://dict.youdao.com/dictvoice?audio={audio_word}&type=2').play()">"""
            f"\U0001f50a</button>"
        )
    if ukphone:
        parts.append(
            f'<span class="phonetic">UK /{escape(ukphone)}/</span>'
            f'<button class="audio-btn" onclick='
            f""""new Audio('https://dict.youdao.com/dictvoice?audio={audio_word}&type=1').play()">"""
            f"\U0001f50a</button>"
        )
    parts.append("</div>")

    # --- Exam tags ---
    exam_types = ec.get("exam_type", [])
    if exam_types:
        parts.append('<div class="exam-tags">')
        for tag in exam_types:
            parts.append(f'<span class="tag">{escape(tag)}</span>')
        parts.append("</div>")

    # --- Definitions (ec) ---
    trs = ec_word.get("trs", [])
    if trs:
        parts.append('<div class="section">')
        parts.append('<div class="section-title">Definitions</div>')
        for tr in trs:
            pos = tr.get("pos", "")
            tran = tr.get("tran", "")
            parts.append(
                f'<div class="tran"><span class="pos">{escape(pos)}</span>'
                f"{escape(tran)}</div>"
            )
        parts.append("</div>")

    # --- Word forms ---
    wfs = ec_word.get("wfs", [])
    if wfs:
        parts.append('<div class="section"><div class="wf-tags">')
        for wf_item in wfs:
            wf = wf_item.get("wf", {})
            name = wf.get("name", "")
            value = wf.get("value", "")
            parts.append(
                f'<span class="wf">{escape(name)}: '
                f'<span class="wf-val">{escape(value)}</span></span>'
            )
        parts.append("</div></div>")

    # --- Phrases ---
    phrs_data = data.get("phrs", {}).get("phrs", [])
    if phrs_data:
        parts.append('<div class="section">')
        parts.append('<div class="section-title">Phrases</div>')
        for phr in phrs_data[:10]:
            hw = phr.get("headword", "")
            tr = phr.get("translation", "")
            parts.append(
                f'<div class="phrase">'
                f'<span class="phrase-word">{escape(hw)}</span>'
                f'<span class="phrase-tran">{escape(tr)}</span></div>'
            )
        parts.append("</div>")

    # --- Synonyms ---
    synos = data.get("syno", {}).get("synos", [])
    if synos:
        parts.append('<div class="section">')
        parts.append('<div class="section-title">Synonyms</div>')
        for s in synos:
            pos = s.get("pos", "")
            ws = ", ".join(s.get("ws", []))
            tran = s.get("tran", "")
            parts.append(
                f'<div class="tran"><span class="pos">{escape(pos)}</span>'
                f'<span class="syno-words">{escape(ws)}</span>'
                f'<span class="phrase-tran"> {escape(tran)}</span></div>'
            )
        parts.append("</div>")

    # --- Example sentences ---
    sents = data.get("blng_sents_part", {}).get("sentence-pair", [])
    if sents:
        parts.append('<div class="section">')
        parts.append('<div class="section-title">Examples</div>')
        for s in sents[:5]:
            en = s.get("sentence", "")
            zh = s.get("sentence-translation", "")
            parts.append(
                f'<div class="example">'
                f'<div class="example-en">{escape(en)}</div>'
                f'<div class="example-zh">{escape(zh)}</div></div>'
            )
        parts.append("</div>")

    # --- Collins ---
    collins_entries = data.get("collins", {}).get("collins_entries", [])
    for ce in collins_entries:
        entries = ce.get("entries", {}).get("entry", [])
        if not entries:
            continue
        parts.append('<div class="section">')
        parts.append('<div class="section-title">Collins</div>')
        for entry in entries:
            for te in entry.get("tran_entry", []):
                pos_entry = te.get("pos_entry", {})
                pos = pos_entry.get("pos", "")
                pos_tips = pos_entry.get("pos_tips", "")
                tran = te.get("tran", "")
                parts.append(
                    f'<div class="collins-entry">'
                    f'<span class="collins-pos">{escape(pos)}</span> '
                    f'<span class="tag">{escape(pos_tips)}</span><br>'
                    f"{_sanitize_html(tran)}</div>"
                )
                # Collins example sentences
                exam_sents = te.get("exam_sents", {}).get("sent", [])
                for sent in exam_sents[:2]:
                    en = sent.get("eng_sent", "")
                    zh = sent.get("chn_sent", "")
                    parts.append(
                        f'<div class="example">'
                        f'<div class="example-en">{escape(en)}</div>'
                        f'<div class="example-zh">{escape(zh)}</div></div>'
                    )
        parts.append("</div>")

    # --- Etymology ---
    etym_list = data.get("etym", {}).get("etyms", {}).get("zh", [])
    if etym_list:
        parts.append('<div class="section">')
        parts.append('<div class="section-title">Etymology</div>')
        for e in etym_list:
            parts.append(f'<div class="etym">{escape(e.get("value", ""))}</div>')
        parts.append("</div>")

    # --- Web translations (ZH→EN fallback when ec is absent) ---
    if not ec_word.get("trs"):
        web_trans = data.get("web_trans", {}).get("web-translation", [])
        if web_trans:
            parts.append('<div class="section">')
            parts.append('<div class="section-title">Translations</div>')
            for wt in web_trans[:10]:
                key = wt.get("key", "")
                values = [t.get("value", "") for t in wt.get("trans", [])]
                if values:
                    parts.append(
                        f'<div class="tran">'
                        f'<span class="phrase-word">{escape(key)}</span>'
                        f'<span class="phrase-tran"> {escape(", ".join(values))}'
                        f"</span></div>"
                    )
            parts.append("</div>")

    # --- Fallback if nothing rendered ---
    if (
        not ec_word.get("trs")
        and not collins_entries
        and not synos
        and not data.get("web_trans", {}).get("web-translation")
    ):
        parts.append(
            f'<div class="fallback">{escape(word)}<br>No definition found</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)
