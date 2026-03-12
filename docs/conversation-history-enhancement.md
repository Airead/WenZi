# Conversation History Enhancement

## Background

VoiceText processes each voice input session independently — the AI enhancement step sees only the current ASR text and has no knowledge of what the user said before. This stateless approach works for isolated sentences but falls short when:

1. **Recurring proper nouns** — The user mentions "萍萍" in one sentence, but ASR transcribes it as "平平". Without prior context, the LLM has no basis to prefer one over the other. If the user already confirmed "萍萍" in a previous turn, that signal is lost.
2. **Topic continuity** — Conversations naturally build on prior context. When the user says "她说今天很开心", the LLM cannot resolve "她" without knowing who was mentioned earlier.
3. **User expression patterns** — Some users consistently use specific phrasing, punctuation styles, or sentence structures. A stateless enhancer cannot adapt to these preferences.

## Motivation

The core insight: **the user's recent confirmed outputs are the highest-quality signal for what they actually mean**.

Unlike raw ASR text (which contains errors) or AI-enhanced text (which may over-correct), the final confirmed output represents the user's true intent — they have reviewed and approved it. By feeding these confirmed outputs back into the enhancement prompt, we give the LLM a running context window that enables:

- **Consistent entity resolution** — Once "平平 → 萍萍" is confirmed, subsequent mentions of "平平" can be correctly resolved.
- **Topic-aware enhancement** — The LLM understands the current conversation topic and can make contextually appropriate decisions.
- **Style adaptation** — The LLM observes how the user actually writes and can match their tone and formatting preferences.

## How It Works

### Recording

Every voice input session is recorded to `~/.config/VoiceText/conversation_history.jsonl`, regardless of mode:

```json
{
    "timestamp": "2026-03-12T10:30:00+00:00",
    "asr_text": "果果今天在公园里遇到了平平。",
    "enhanced_text": "果果今天在公园里遇到了平平。",
    "final_text": "果果今天在公园里遇到了萍萍。",
    "enhance_mode": "proofread",
    "preview_enabled": true
}
```

Both direct mode (`preview_enabled: false`) and preview mode (`preview_enabled: true`) sessions are recorded. This ensures no data is lost, even if the injection policy changes later.

### Filtering: Why Only Preview-Confirmed Records

Only records with `preview_enabled: true` are injected into the AI prompt. The reasoning:

- **Preview mode** — The user sees the AI output, can edit it, and explicitly confirms the final text. This confirmed text is reliable.
- **Direct mode** — Text is typed immediately without review. The user never validated the output, so it may contain uncorrected ASR errors or AI over-corrections. Injecting unverified text would propagate errors.

This is a deliberate data quality decision: **a smaller set of verified data is more valuable than a larger set of unverified data**.

### Context Injection

When conversation history is enabled, the enhancement prompt is augmented with recent history. The format is designed to be token-efficient:

```
---
以下是用户近期的对话历史，用于了解表达习惯和话题上下文。
每条均为一行，若语音识别与最终确认不同则用→分隔（识别→确认），相同则无→。

- 将是否开启对话历史注入的功能，做一个开关放在菜单栏里。
- 现在测试一下历史上下文注入的功能。
- 果果今天在公园里遇到了平平。 → 果果今天在公园里遇到了萍萍。
- 平平对果果说我今天吃了面条。 → 萍萍对果果说我今天吃了面条。
---
```

Key design choices for the prompt format:

- **One line per entry** — Minimizes token usage.
- **Arrow notation for corrections** — Only shown when ASR and final text differ, making correction patterns immediately visible to the LLM.
- **No arrow when identical** — Avoids redundant repetition of the same text.
- **Only ASR + final text** — The AI-enhanced intermediate text is omitted from injection to save tokens. The LLM only needs to see what the user actually said (ASR) and what they actually meant (final).

### Integration with Other Context Sources

Conversation history is injected into the system prompt **after** vocabulary context, following the same pattern:

```
[Mode prompt]                    ← base enhancement instructions
[Vocabulary context]             ← relevant terms from user's vocabulary (if enabled)
[Conversation history context]   ← recent confirmed outputs (if enabled)
```

Each context source is independently toggleable and gracefully degrades — if history retrieval fails, enhancement proceeds without it.

## Architecture

```
Voice Input
     │
     ▼
┌──────────┐     ┌───────────────────────────────────┐
│ ASR      │────►│ conversation_history.jsonl         │
└──────────┘     │  (append-only, all sessions)       │
     │           └─────────────────┬─────────────────┘
     ▼                             │
┌──────────┐                       │ get_recent(n=10)
│ Enhancer │◄──────────────────────┘ filter: preview_enabled=true
│          │
│ system_prompt = base_prompt      │
│            + vocab_context       │
│            + history_context ◄───┘
│          │
│          │──► LLM ──► enhanced text
└──────────┘
     │
     ▼
┌──────────────────┐
│ Preview Panel    │──► user confirms ──► type_text
│ (preview mode)   │                        │
└──────────────────┘                        ▼
                                   log(preview_enabled=true)
```

## Configuration

In `config.json` under `ai_enhance`:

```json
{
    "conversation_history": {
        "enabled": false,
        "max_entries": 10
    }
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Toggle conversation history injection |
| `max_entries` | int | `10` | Maximum number of recent entries to inject |

The toggle is also available as a top-level item in the menubar: **Conversation History**.

## Key Files

| File | Purpose |
|---|---|
| `src/voicetext/conversation_history.py` | JSONL recording, reading, filtering, and prompt formatting |
| `src/voicetext/enhancer.py` | Integrates history context into enhancement prompts |
| `src/voicetext/app.py` | Records sessions in both output paths; menu toggle |
| `src/voicetext/config.py` | Default configuration for conversation history |
