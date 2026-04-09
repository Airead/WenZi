#!/usr/bin/env python3
"""Generate 200 random manual vocabulary entries for UI testing."""

import json
import random
from datetime import UTC, datetime, timedelta

OUTPUT = "/Users/fanrenhao/.local/share/WenZi/manual_vocabulary_test.json"

# --- Realistic term/variant pools ---
TERM_VARIANTS = [
    ("Claude", ["Cloud", "cloud", "克劳德"]),
    ("Kubernetes", ["库伯尼特斯", "k8s那个", "Cube Nets"]),
    ("pytest", ["py test", "派test", "pie test"]),
    ("Docker", ["多克", "dark", "docker那个"]),
    ("Terraform", ["太啊form", "terror form", "terra from"]),
    ("React", ["riac", "里act"]),
    ("TypeScript", ["type script", "TS那个", "太script"]),
    ("PostgreSQL", ["post格瑞", "post grass", "Postgres Q"]),
    ("Nginx", ["engine X", "N金克斯", "en jinx"]),
    ("Redis", ["瑞dis", "read is", "re this"]),
    ("GraphQL", ["graph QL", "格拉夫Q", "graph cool"]),
    ("webpack", ["web pack", "微pack", "web趴克"]),
    ("MongoDB", ["mongo DB", "蒙go", "mango DB"]),
    ("FastAPI", ["fast API", "发思t API", "fast a pie"]),
    ("PyObjC", ["py object", "py OBJ C", "派object"]),
    ("WenZi", ["闻字", "文字", "wen zi"]),
    ("worktree", ["walk tree", "work T", "我可tree"]),
    ("worktrunk", ["walk trunk", "work trunk", "我trunk"]),
    ("gitignore", ["get ignore", "git in now", "git ignite"]),
    ("venv", ["DV", "V en V", "the env"]),
    ("Homebrew", ["home brew", "红brew", "home bro"]),
    ("Xcode", ["X code", "叉code", "ex code"]),
    ("SwiftUI", ["swift UI", "思维UI", "swift you I"]),
    ("CoreData", ["core data", "扣data", "core达他"]),
    ("CLAUDE.md", ["Cloud还没地", "cloud MD", "克劳MD"]),
    ("Ansible", ["安撕boo", "answer ball", "an sible"]),
    ("Prometheus", ["pro me the us", "普罗米修斯", "promo thesis"]),
    ("Grafana", ["格拉法那", "graph ana", "gra花那"]),
    ("Jenkins", ["金肯斯", "jen kins", "jenkins那个"]),
    ("Elasticsearch", ["elastic search", "弹性搜索", "ela stick"]),
    ("RabbitMQ", ["rabbit MQ", "兔子MQ", "rabbit M"]),
    ("Celery", ["芹菜", "cell ery", "salary"]),
    ("Django", ["姜go", "D jango", "d将go"]),
    ("Flask", ["弗拉斯克", "flash", "f拉斯克"]),
    ("NumPy", ["num pie", "南派", "number py"]),
    ("Pandas", ["胖das", "pan das", "panda S"]),
    ("Jupyter", ["九pi ter", "jew peter", "Jupiter"]),
    ("TensorFlow", ["tensor flow", "天梭flow", "tensor flo"]),
    ("PyTorch", ["派torch", "py 托奇", "pie torch"]),
    ("CUDA", ["酷大", "coo da", "Q da"]),
    ("MLX", ["ML X", "M了X", "em el X"]),
    ("whisper", ["we spur", "微思per", "whi sper"]),
    ("FunASR", ["fun ASR", "放ASR", "fan a SR"]),
    ("STT", ["set", "S T T", "思TT"]),
    ("ASR", ["vs啊那", "a SR", "啊SR"]),
    ("LLM", ["L了M", "LM那个", "el el em"]),
    ("API", ["a P I", "啊PI", "API那个"]),
    ("OAuth", ["oh auth", "欧auth", "o off"]),
    ("JWT", ["JW T", "就T", "J W T"]),
    ("CORS", ["course", "扣s", "cors那个"]),
]

SOURCES = ["asr", "llm", "user"]
SOURCE_WEIGHTS = [0.5, 0.3, 0.2]

APP_BUNDLE_IDS = [
    "com.googlecode.iterm2",
    "com.apple.dt.Xcode",
    "com.microsoft.VSCode",
    "com.apple.Safari",
    "com.apple.Notes",
    "com.tinyspeck.slackmacgap",
    "com.apple.Terminal",
    "md.obsidian",
    "com.jetbrains.intellij",
    "com.cursor.Cursor",
    "",  # empty for some entries
]

ASR_MODELS = [
    "on-device",
    "whisper-large-v3",
    "whisper-large-v3-turbo",
    "",
]

LLM_MODELS = [
    "zai / glm-5",
    "zai / glm-5.1",
    "zai / glm-4.7",
    "zai / glm-4.7-flash",
    "zai-proxy / glm-4.7",
    "minimax / MiniMax-M2.5-highspeed",
    "minimax / MiniMax-M2.5",
    "deepseek / deepseek-chat",
    "ollama / qwen2.5:7b",
    "oMLX / Qwen3.5-9B-4bit",
    "",
]

ENHANCE_MODES = [
    "proofread",
    "translate",
    "format",
    "off",
    "",
]

# Four hit-tracking metrics and their context key prefixes.
METRICS = ["asr_miss", "asr_hit", "llm_hit", "llm_miss"]

NOW = datetime.now(UTC)


def random_ts(days_back_max: int = 90) -> str:
    delta = timedelta(
        days=random.randint(0, days_back_max),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    return (NOW - delta).isoformat()


def generate_stats(asr_model: str, llm_model: str, app_bundle_id: str) -> list[dict]:
    """Generate realistic bucketed stats rows for one entry."""
    stats: list[dict] = []

    # ~30% of entries have no stats at all (brand new / unused)
    if random.random() < 0.3:
        return stats

    # Build possible context keys for ASR and LLM dimensions
    asr_contexts = []
    if asr_model:
        asr_contexts.append(f"asr:{asr_model}")
    if app_bundle_id:
        asr_contexts.append(f"app:{app_bundle_id}")

    llm_contexts = []
    if llm_model:
        llm_contexts.append(f"llm:{llm_model}")
    if app_bundle_id:
        llm_contexts.append(f"app:{app_bundle_id}")

    # ASR dimension: split total encounters into asr_miss and asr_hit.
    # miss_rate varies per entry — some words ASR handles well, others poorly.
    asr_miss_rate = random.betavariate(2, 2)  # 0.0–1.0, centered ~0.5
    for ctx in asr_contexts:
        if random.random() < 0.7:
            total = random.choices(
                [1, 2, 3, 5, 8, 12, 20],
                weights=[15, 20, 20, 20, 12, 8, 5],
                k=1,
            )[0]
            miss_count = int(total * asr_miss_rate + 0.5)
            hit_count = total - miss_count
            if miss_count > 0:
                stats.append({
                    "metric": "asr_miss",
                    "context_key": ctx,
                    "count": miss_count,
                    "last_time": random_ts(30),
                })
            if hit_count > 0:
                stats.append({
                    "metric": "asr_hit",
                    "context_key": ctx,
                    "count": hit_count,
                    "last_time": random_ts(30),
                })

    # LLM dimension: derive from asr_miss counts (phase 2 only runs after phase 1)
    # Each asr_miss event leads to either llm_hit or llm_miss, so their sum ≈ asr_miss.
    # Vary the LLM correction rate per entry to create realistic diversity.
    llm_correct_rate = random.betavariate(2, 2)  # 0.0–1.0, centered ~0.5
    asr_miss_rows = [s for s in stats if s["metric"] == "asr_miss"]
    for asr_row in asr_miss_rows:
        total = asr_row["count"]
        hit_count = int(total * llm_correct_rate + 0.5)
        miss_count = total - hit_count
        # Map asr context key to corresponding llm context key
        ctx = asr_row["context_key"]
        if ctx.startswith("asr:") and llm_contexts:
            llm_ctx = random.choice(llm_contexts)
        else:
            llm_ctx = ctx  # app: keys stay the same
        if hit_count > 0:
            stats.append({
                "metric": "llm_hit",
                "context_key": llm_ctx,
                "count": hit_count,
                "last_time": random_ts(30),
            })
        if miss_count > 0:
            stats.append({
                "metric": "llm_miss",
                "context_key": llm_ctx,
                "count": miss_count,
                "last_time": random_ts(30),
            })

    return stats


def generate_entries(count: int = 200) -> list[dict]:
    entries = []
    for _ in range(count):
        term, variants = random.choice(TERM_VARIANTS)
        variant = random.choice(variants)
        source = random.choices(SOURCES, weights=SOURCE_WEIGHTS, k=1)[0]

        first_seen = random_ts(90)
        last_updated_dt = datetime.fromisoformat(first_seen) + timedelta(
            days=random.randint(0, 30),
            hours=random.randint(0, 12),
        )
        if last_updated_dt > NOW:
            last_updated_dt = NOW
        last_updated = last_updated_dt.isoformat()

        # Most entries have both ASR and LLM models (~80%)
        asr_model = random.choice([m for m in ASR_MODELS if m]) if random.random() < 0.8 else ""
        llm_model = random.choice([m for m in LLM_MODELS if m]) if random.random() < 0.8 else ""
        app_bundle_id = random.choice(APP_BUNDLE_IDS)

        entries.append(
            {
                "term": term,
                "variant": variant,
                "source": source,
                "frequency": random.randint(1, 15),
                "first_seen": first_seen,
                "last_updated": last_updated,
                "app_bundle_id": app_bundle_id,
                "asr_model": asr_model,
                "llm_model": llm_model,
                "enhance_mode": random.choice(ENHANCE_MODES),
                "stats": generate_stats(asr_model, llm_model, app_bundle_id),
            }
        )
    return entries


def main() -> None:
    entries = generate_entries(200)
    data = {"version": 2, "entries": entries}
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total_stats = sum(len(e["stats"]) for e in entries)
    with_stats = sum(1 for e in entries if e["stats"])
    print(f"Generated {len(entries)} entries ({with_stats} with stats, {total_stats} stat rows) → {OUTPUT}")


if __name__ == "__main__":
    main()
