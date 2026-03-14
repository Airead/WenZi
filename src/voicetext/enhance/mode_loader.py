"""Load AI enhancement mode definitions from external Markdown files."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MODE_OFF = "off"

DEFAULT_MODES_DIR = os.path.join("~", ".config", "VoiceText", "enhance_modes")


@dataclass
class ModeDefinition:
    """A single enhancement mode definition."""

    mode_id: str
    label: str
    prompt: str
    order: int = 50
    steps: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.steps is None:
            self.steps = []


_BUILTIN_MODES: Dict[str, ModeDefinition] = {
    "proofread": ModeDefinition(
        mode_id="proofread",
        label="纠错润色",
        prompt=(
            "你是一个语音识别文本纠错助手，支持中文和英文。你只做纠错，不做翻译、不做改写、不做润色。\n"
            "\n"
            "用户输入来自语音识别（Whisper，已启用标点恢复），可能包含谐音字、同音字替换、"
            "吞字漏字等识别错误。请根据输入语言自动适配纠错规则。\n"
            "\n"
            "规则（按优先级排列）：\n"
            "1. 输出语言与当前这条输入的语言保持一致，不因对话历史语言不同而翻译\n"
            "2. 不确定的词保留原文，不猜测、不脑补未说出的内容；宁可少改，不要错改\n"
            "3. 命令、路径、文件名、URL、代码标识符、环境变量保留字面形式，不做自然语言化处理\n"
            "4. 保持原文语义和口语风格，只修正明显的 ASR 识别错误（错字、同音字、分词错误）\n"
            "5. 专有名词使用标准写法和大小写（如 CUDA、GitHub、Ethereum），不保留 ASR 的错误拼写\n"
            "6. 重点关注计算机、AI、区块链、金融领域的专业术语，但仅在上下文充分支持且发音或拼写接近时才纠正\n"
            "7. 中英文混排时加一个半角空格，纯中文或纯英文内部按各自规范\n"
            "8. 中文语境使用全角标点，英文语境使用半角标点；文本末尾不加句号等陈述性标点，但保留问号、感叹号等语气标点\n"  # noqa: E501
            "9. 在明显缺失分隔的长句中补充标点，已有标点的部分仅修正明显错误\n"
            "10. 去除无语义作用的口语填充词（中文如\u201c呢\u201d\u201c啊\u201d\u201c那个\u201d，英文如\u201cum\u201d\u201cuh\u201d\u201clike\u201d\u201cyou know\u201d），"  # noqa: E501
            "但保留有确认、回应、连接作用的用法（如\u201c好，那我们...\u201d中的\u201c好\u201d）\n"
            "11. 数字默认使用阿拉伯数字，保留约定俗成的表达（中文如\u201c一带一路\u201d，英文如\u201cArea 51\u201d）\n"
            "12. 对话历史和词库仅作为消歧参考，当证据不足时以当前句字面内容为准，不得用历史内容覆盖当前句的数字、编号、实体名\n"  # noqa: E501
            "13. 直接输出修正后的文本，不要添加任何解释或说明"
        ),
        order=10,
    ),
    "translate_en": ModeDefinition(
        mode_id="translate_en",
        label="翻译为英文",
        prompt=(
            "You are a Chinese-to-English translator. "
            "The user's input comes from ASR and may contain homophone errors "
            "or misrecognized characters \u2014 infer the intended meaning from context.\n"
            "\n"
            "Rules:\n"
            "1. Translate into natural, fluent English; preserve the original meaning and tone\n"
            "2. Keep proper nouns, brand names, and technical terms in their standard English form\n"
            "3. Match the register: casual input \u2192 casual English, formal input \u2192 formal English\n"
            "4. Output only the translated text without any explanation"
        ),
        order=20,
    ),
    "translate_en_plus": ModeDefinition(
        mode_id="translate_en_plus",
        label="润色+翻译EN",
        prompt="",
        order=25,
        steps=["proofread", "translate_en"],
    ),
    "commandline_master": ModeDefinition(
        mode_id="commandline_master",
        label="命令行大神",
        prompt=(
            "你是命令行专家，精通 Linux 核心工具及 FFmpeg、OpenSSL、Docker 等常用软件。"
            "用户输入来自 ASR，可能包含谐音字等错误，请推断真实意图。\n"
            "\n"
            "将用户的自然语言需求转换为最简洁、可直接执行的命令行命令。\n"
            "\n"
            "规则：\n"
            "1. 优先使用管道符组合命令，追求单行解决\n"
            "2. 只输出命令本身，禁止任何解释、注释或 Markdown 格式\n"
            "\n"
            "示例：\n"
            '- "显示所有 python 进程号" → ps aux | grep python | grep -v grep | awk \'{print $2}\'\n'
            '- "把当前目录视频转 mp3" → for i in *.mp4; do ffmpeg -i "$i" -vn "${i%.mp4}.mp3"; done\n'
            '- "查本机公网 IP" → curl ifconfig.me\n'
            '- "生成 32 位随机十六进制" → openssl rand -hex 16'
        ),
        order=30,
    ),
}


def parse_mode_file(file_path: str) -> Optional[ModeDefinition]:
    """Parse a Markdown mode file with optional YAML front matter.

    Returns None if the file is empty or unreadable.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.warning("Failed to read mode file %s: %s", file_path, e)
        return None

    if not content.strip():
        return None

    basename = os.path.splitext(os.path.basename(file_path))[0]
    label = basename
    order = 50
    steps: List[str] = []
    prompt = content.strip()

    # Try to parse front matter delimited by ---
    parts = content.split("---", 2)
    if len(parts) >= 3 and not parts[0].strip():
        front_matter = parts[1]
        body = parts[2].strip()

        # Extract label
        label_match = re.search(r"^label:\s*(.+)$", front_matter, re.MULTILINE)
        if label_match:
            label = label_match.group(1).strip()

        # Extract order
        order_match = re.search(r"^order:\s*(\d+)$", front_matter, re.MULTILINE)
        if order_match:
            order = int(order_match.group(1))

        # Extract steps (comma-separated mode_id list)
        steps_match = re.search(r"^steps:\s*(.+)$", front_matter, re.MULTILINE)
        if steps_match:
            steps = [s.strip() for s in steps_match.group(1).split(",") if s.strip()]

        if body:
            prompt = body

    return ModeDefinition(mode_id=basename, label=label, prompt=prompt, order=order, steps=steps)


def load_modes(modes_dir: Optional[str] = None) -> Dict[str, ModeDefinition]:
    """Load enhancement modes from a directory of Markdown files.

    Falls back to builtin defaults if the directory does not exist or
    contains no valid .md files.
    """
    if modes_dir is None:
        modes_dir = DEFAULT_MODES_DIR
    expanded = os.path.expanduser(modes_dir)

    modes: Dict[str, ModeDefinition] = {}

    if os.path.isdir(expanded):
        for name in os.listdir(expanded):
            if not name.endswith(".md"):
                continue
            path = os.path.join(expanded, name)
            mode_def = parse_mode_file(path)
            if mode_def is not None:
                modes[mode_def.mode_id] = mode_def

    if not modes:
        return dict(_BUILTIN_MODES)

    return modes


def ensure_default_modes(modes_dir: Optional[str] = None) -> str:
    """Ensure each builtin default mode has a corresponding Markdown file.

    Missing builtin mode files are created; existing ones are never overwritten.
    Returns the expanded directory path.
    """
    if modes_dir is None:
        modes_dir = DEFAULT_MODES_DIR
    expanded = os.path.expanduser(modes_dir)

    os.makedirs(expanded, exist_ok=True)

    for mode_id, mode_def in _BUILTIN_MODES.items():
        file_path = os.path.join(expanded, f"{mode_id}.md")
        if os.path.exists(file_path):
            continue
        lines = [
            "---",
            f"label: {mode_def.label}",
            f"order: {mode_def.order}",
        ]
        if mode_def.steps:
            lines.append(f"steps: {', '.join(mode_def.steps)}")
        lines.append("---")
        lines.append(mode_def.prompt)
        lines.append("")
        content = "\n".join(lines)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("Created default mode file: %s", file_path)

    return expanded


def get_sorted_modes(modes: Dict[str, ModeDefinition]) -> List[Tuple[str, str]]:
    """Return (mode_id, label) pairs sorted by order."""
    sorted_modes = sorted(modes.values(), key=lambda m: (m.order, m.mode_id))
    return [(m.mode_id, m.label) for m in sorted_modes]
