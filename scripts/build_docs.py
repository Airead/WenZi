#!/usr/bin/env python3
"""Build HTML documentation pages from docs/*.md files.

Reads Markdown files from docs/, converts them to HTML using the `markdown`
library, wraps each in a template that matches the existing site style, and
writes output to site/docs/ (English) and site/zh/docs/ (Chinese).

Landing pages (site/index.html, site/zh/index.html) are NOT touched.

Usage:
    pip install markdown
    python scripts/build_docs.py          # build all docs
    python scripts/build_docs.py --clean  # remove generated HTML first
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import markdown
    from markdown.extensions.toc import TocExtension
except ImportError:
    print("Error: 'markdown' package is required. Install it with:")
    print("  pip install markdown")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
SITE_DIR = ROOT / "site"

# ---------------------------------------------------------------------------
# Document registry — defines which docs to publish and their metadata.
#
# Each entry maps a slug (used as the output filename) to:
#   - src_en / src_zh: source markdown filenames in docs/
#   - title_en / title_zh: page <title>
#   - desc_en / desc_zh: subtitle shown below the h1
#   - icon: emoji for the doc card on the landing page
#   - nav_order: sort order in navigation (lower = first)
# ---------------------------------------------------------------------------
DOC_REGISTRY: list[dict] = [
    {
        "slug": "user-guide",
        "src_en": "user-guide.md",
        "src_zh": "user-guide-zh.md",
        "title_en": "User Guide",
        "title_zh": "用户指南",
        "desc_en": "Progressive guide from first launch to advanced usage.",
        "desc_zh": "从首次启动到高级用法的渐进式指南。",
        "icon": "&#128214;",
        "nav_order": 1,
    },
    {
        "slug": "error-correction",
        "src_en": "error-correction.md",
        "src_zh": "error-correction-zh.md",
        "title_en": "Why Error Correction Is So Powerful",
        "title_zh": "为什么纠错能力这么强",
        "desc_en": "Five layers of correction that get smarter every time you use it.",
        "desc_zh": "五层纠错机制，越用越聪明。",
        "icon": "&#128170;",
        "nav_order": 2,
    },
    {
        "slug": "configuration",
        "src_en": "configuration.md",
        "src_zh": "configuration-zh.md",
        "title_en": "Configuration Reference",
        "title_zh": "配置参考",
        "desc_en": "All configuration options explained.",
        "desc_zh": "所有配置选项详解。",
        "icon": "&#9881;",
        "nav_order": 3,
    },
    {
        "slug": "enhance-modes",
        "src_en": "enhance-modes.md",
        "src_zh": "enhance-modes-zh.md",
        "title_en": "AI Enhancement Modes",
        "title_zh": "AI 增强模式",
        "desc_en": "Define how AI post-processes your transcriptions.",
        "desc_zh": "定义 AI 如何后处理你的转录文本。",
        "icon": "&#10024;",
        "nav_order": 4,
    },
    {
        "slug": "enhance-mode-examples",
        "src_en": "enhance-mode-examples.md",
        "src_zh": "enhance-mode-examples-zh.md",
        "title_en": "Enhancement Mode Examples",
        "title_zh": "增强模式示例",
        "desc_en": "Ready-to-use enhancement mode templates.",
        "desc_zh": "即用型增强模式模板。",
        "icon": "&#128203;",
        "nav_order": 5,
    },
    {
        "slug": "prompt-optimization",
        "src_en": "prompt-optimization-workflow.md",
        "src_zh": "prompt-optimization-workflow-zh.md",
        "title_en": "Prompt Optimization Workflow",
        "title_zh": "Prompt 优化工作流",
        "desc_en": "Systematically improve AI enhancement quality.",
        "desc_zh": "系统化地提升 AI 增强质量。",
        "icon": "&#128161;",
        "nav_order": 6,
    },
    {
        "slug": "provider-model-guide",
        "src_en": "provider-model-guide.md",
        "src_zh": "provider-model-guide-zh.md",
        "title_en": "Provider & Model Guide",
        "title_zh": "提供商与模型指南",
        "desc_en": "Configure ASR and LLM providers.",
        "desc_zh": "配置 ASR 和 LLM 提供商。",
        "icon": "&#128268;",
        "nav_order": 7,
    },
    {
        "slug": "vocabulary-embedding-retrieval",
        "src_en": "vocabulary-embedding-retrieval.md",
        "src_zh": "vocabulary-embedding-retrieval-zh.md",
        "title_en": "Vocabulary & Embedding Retrieval",
        "title_zh": "词汇表与向量检索",
        "desc_en": "Personal vocabulary index for better correction.",
        "desc_zh": "个人词汇索引以改善纠错。",
        "icon": "&#128218;",
        "nav_order": 8,
    },
    {
        "slug": "conversation-history-enhancement",
        "src_en": "conversation-history-enhancement.md",
        "src_zh": "conversation-history-enhancement-zh.md",
        "title_en": "Conversation History Enhancement",
        "title_zh": "会话历史增强",
        "desc_en": "Topic continuity and entity resolution via history.",
        "desc_zh": "通过历史记录实现话题连续性和实体解析。",
        "icon": "&#128172;",
        "nav_order": 9,
    },
    {
        "slug": "scripting",
        "src_en": "scripting.md",
        "src_zh": "scripting-zh.md",
        "title_en": "Scripting",
        "title_zh": "脚本系统",
        "desc_en": "Automate macOS tasks with Python scripts — leader keys, hotkeys, alerts, and more.",
        "desc_zh": "使用 Python 脚本自动化 macOS 任务——Leader 键、快捷键、提示、剪贴板等。",
        "icon": "&#128187;",
        "nav_order": 10,
    },
]

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------
DOC_INLINE_CSS = """\
    .doc-container { max-width: 800px; margin: 0 auto; padding: 80px 24px 60px; }
    .doc-container h1 { font-size: 2.2rem; font-weight: 800; margin-bottom: 8px; }
    .doc-container .doc-subtitle { font-size: 1.1rem; margin-bottom: 32px; color: var(--text-secondary); }
    .doc-container h2 { font-size: 1.6rem; margin-top: 48px; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 1px solid var(--card-border); }
    .doc-container h3 { font-size: 1.2rem; margin-top: 32px; margin-bottom: 12px; }
    .doc-container h4 { font-size: 1.05rem; margin-top: 24px; margin-bottom: 8px; }
    .doc-container p { margin-bottom: 16px; color: var(--text-secondary); }
    .doc-container ul, .doc-container ol { margin-bottom: 16px; padding-left: 24px; color: var(--text-secondary); }
    .doc-container li { margin-bottom: 8px; }
    .doc-container li > ul, .doc-container li > ol { margin-top: 8px; margin-bottom: 0; }
    .doc-container blockquote { border-left: 3px solid var(--purple); padding: 12px 16px; margin: 16px 0; background: var(--bg-alt); border-radius: 0 8px 8px 0; color: var(--text-secondary); }
    .doc-container code { background: var(--code-bg); padding: 2px 6px; border-radius: 4px; font-size: 0.88rem; }
    .doc-container pre { background: var(--code-bg); border-radius: 8px; padding: 16px; overflow-x: auto; font-size: 0.88rem; line-height: 1.6; margin-bottom: 16px; }
    .doc-container pre code { background: none; padding: 0; }
    .doc-container strong { color: var(--text); }
    .doc-container img { border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin: 16px 0; }
    .doc-container hr { border: none; border-top: 1px solid var(--card-border); margin: 32px 0; }
    .doc-container .toc { background: var(--bg-alt); border-radius: 12px; padding: 24px 28px; margin-bottom: 40px; }
    .doc-container .toc h2 { margin-top: 0 !important; border-bottom: none !important; font-size: 1.2rem; }
    .doc-container .toc ul { list-style: none; padding-left: 0; }
    .doc-container .toc li { margin-bottom: 6px; }
    .doc-container .toc a { color: var(--purple); }
    .table-wrapper { overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 16px; }
    .doc-container table { width: 100%; border-collapse: collapse; background: var(--card-bg); border-radius: 8px; overflow: hidden; border: 1px solid var(--card-border); }
    .doc-container th, .doc-container td { padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--card-border); }
    .doc-container th { background: var(--purple); color: #fff; font-weight: 600; font-size: 0.95rem; }
    .doc-container tr:last-child td { border-bottom: none; }
    .doc-container tr:hover td { background: var(--bg-alt); }
    .doc-nav { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 48px; }
    .doc-nav-card { display: block; background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 12px; padding: 20px 24px; transition: box-shadow 0.2s; text-decoration: none; }
    .doc-nav-card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.08); text-decoration: none; }
    .doc-nav-card h3 { color: var(--text); margin-bottom: 4px; font-size: 1rem; }
    .doc-nav-card p { color: var(--text-secondary); margin: 0; font-size: 0.9rem; }
    @media (max-width: 600px) { .doc-nav { grid-template-columns: 1fr; } }"""


def _html_template(
    *,
    lang: str,
    title: str,
    body_html: str,
    toc_html: str,
    subtitle: str,
    nav_prev: dict | None,
    nav_next: dict | None,
    lang_switch_href: str,
    prefix: str,
) -> str:
    """Render the full HTML page."""
    back_label = "&larr; Back" if lang == "en" else "&larr; 返回"
    toc_title = "Table of Contents" if lang == "en" else "目录"
    github_label = "GitHub"

    toc_section = ""
    if toc_html.strip():
        toc_section = f"""
    <div class="toc">
      <h2>{toc_title}</h2>
      {toc_html}
    </div>"""

    nav_cards = ""
    if nav_prev or nav_next:
        prev_card = ""
        next_card = ""
        if nav_prev:
            prev_card = f"""\
      <a href="{nav_prev['slug']}.html" class="doc-nav-card">
        <h3>&larr; {nav_prev['title']}</h3>
        <p>{nav_prev['desc']}</p>
      </a>"""
        else:
            prev_card = "      <div></div>"
        if nav_next:
            next_card = f"""\
      <a href="{nav_next['slug']}.html" class="doc-nav-card">
        <h3>{nav_next['title']} &rarr;</h3>
        <p>{nav_next['desc']}</p>
      </a>"""
        else:
            next_card = "      <div></div>"
        nav_cards = f"""
    <div class="doc-nav">
{prev_card}
{next_card}
    </div>"""

    return f"""\
<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — VoiceText</title>
  <link rel="icon" type="image/png" href="{prefix}images/icon.png">
  <link rel="stylesheet" href="{prefix}css/style.css">
  <style>
{DOC_INLINE_CSS}
  </style>
</head>
<body>

  <header class="site-header">
    <nav class="nav container">
      <a href="{prefix}" class="nav-brand">
        <img src="{prefix}images/icon.png" alt="VoiceText icon">
        VoiceText
      </a>
      <ul class="nav-links">
        <li><a href="{prefix}">{back_label}</a></li>
        <li><a href="{lang_switch_href}" class="lang-switch">{"中文" if lang == "en" else "English"}</a></li>
        <li><a href="https://github.com/Airead/VoiceText" target="_blank">{github_label}</a></li>
      </ul>
    </nav>
  </header>

  <article class="doc-container">
    <h1>{title}</h1>
    <p class="doc-subtitle">{subtitle}</p>
{toc_section}

{body_html}
{nav_cards}
  </article>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# Markdown processing
# ---------------------------------------------------------------------------
def _convert_md(md_text: str) -> tuple[str, str]:
    """Convert markdown to (body_html, toc_html).

    The first H1 heading is stripped (it becomes the page title).
    """
    # Strip first H1
    md_text = re.sub(r"^#\s+.+\n*", "", md_text, count=1)

    # Remove the manual TOC section (lines starting with "## Table of Contents"
    # until the next ## or end)
    md_text = re.sub(
        r"^## (?:Table of Contents|目录)\s*\n(?:(?!^## ).*\n)*",
        "",
        md_text,
        flags=re.MULTILINE,
    )

    md = markdown.Markdown(
        extensions=[
            "fenced_code",
            "tables",
            "sane_lists",
            TocExtension(permalink=False, toc_depth="2-3"),
        ]
    )
    body_html = md.convert(md_text)
    toc_html = md.toc

    # Wrap tables in a scrollable wrapper
    body_html = re.sub(
        r"(<table.*?</table>)",
        r'<div class="table-wrapper">\1</div>',
        body_html,
        flags=re.DOTALL,
    )

    # Make headings clickable anchor links
    body_html = re.sub(
        r"<(h[234])\s+id=\"([^\"]+)\">(.+?)</\1>",
        r'<\1 id="\2"><a href="#\2" class="heading-anchor">\3</a></\1>',
        body_html,
    )

    return body_html, toc_html


# ---------------------------------------------------------------------------
# Build logic
# ---------------------------------------------------------------------------
def build_doc(entry: dict, registry: list[dict]) -> None:
    """Build English and Chinese HTML for one doc entry."""
    idx = registry.index(entry)

    for lang in ("en", "zh"):
        src_key = f"src_{lang}"
        title_key = f"title_{lang}"
        desc_key = f"desc_{lang}"

        src_file = DOCS_DIR / entry[src_key]
        if not src_file.exists():
            print(f"  SKIP {src_file.name} (not found)")
            continue

        md_text = src_file.read_text(encoding="utf-8")
        body_html, toc_html = _convert_md(md_text)

        # Navigation: prev/next
        nav_prev = None
        nav_next = None
        if idx > 0:
            p = registry[idx - 1]
            nav_prev = {
                "slug": p["slug"],
                "title": p[title_key],
                "desc": p[desc_key],
            }
        if idx < len(registry) - 1:
            n = registry[idx + 1]
            nav_next = {
                "slug": n["slug"],
                "title": n[title_key],
                "desc": n[desc_key],
            }

        # Language switch href
        if lang == "en":
            lang_switch_href = f"../zh/docs/{entry['slug']}.html"
            out_dir = SITE_DIR / "docs"
            prefix = "../"
        else:
            lang_switch_href = f"../../docs/{entry['slug']}.html"
            out_dir = SITE_DIR / "zh" / "docs"
            prefix = "../../"

        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{entry['slug']}.html"

        html = _html_template(
            lang="en" if lang == "en" else "zh",
            title=entry[title_key],
            body_html=body_html,
            toc_html=toc_html,
            subtitle=entry[desc_key],
            nav_prev=nav_prev,
            nav_next=nav_next,
            lang_switch_href=lang_switch_href,
            prefix=prefix,
        )

        out_file.write_text(html, encoding="utf-8")
        print(f"  OK {out_file.relative_to(ROOT)}")


def clean() -> None:
    """Remove generated HTML files (not index.html)."""
    for d in [SITE_DIR / "docs", SITE_DIR / "zh" / "docs"]:
        if d.exists():
            for f in d.glob("*.html"):
                f.unlink()
                print(f"  DEL {f.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build HTML docs from Markdown")
    parser.add_argument("--clean", action="store_true", help="Remove generated files")
    args = parser.parse_args()

    # Sort registry by nav_order
    registry = sorted(DOC_REGISTRY, key=lambda e: e["nav_order"])

    if args.clean:
        print("Cleaning generated HTML...")
        clean()
        print()

    print("Building docs...")
    for entry in registry:
        build_doc(entry, registry)

    print(f"\nDone. {len(registry)} docs x 2 languages.")


if __name__ == "__main__":
    main()
