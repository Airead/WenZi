# AI Enhancement Modes Guide

VoiceText uses AI enhancement modes to post-process transcribed text. Each mode is defined as an independent Markdown file stored in `~/.config/VoiceText/enhance_modes/`. You can add, edit, or remove modes without modifying any code.

## Table of Contents

- [How It Works](#how-it-works)
- [File Format](#file-format)
- [Built-in Modes](#built-in-modes)
- [Add a New Mode](#add-a-new-mode)
- [Edit an Existing Mode](#edit-an-existing-mode)
- [Remove a Mode](#remove-a-mode)
- [Tips](#tips)

## How It Works

```
Speech -> ASR transcription -> Enhancement mode (LLM) -> Final text
```

1. On startup, VoiceText ensures the built-in mode files exist in the modes directory. Missing built-in files are recreated automatically; existing files are never overwritten.
2. All `.md` files in the directory are loaded and appear in the **AI Enhance** menu.
3. When an enhancement mode is active, the transcribed text is sent to the configured LLM with the mode's prompt as the system message.

## File Format

Each `.md` file uses a simple YAML front matter followed by the prompt body:

```markdown
---
label: Display Name
order: 50
---
System prompt content goes here.
You can use multiple lines.
```

| Field   | Required | Description                                              |
|---------|----------|----------------------------------------------------------|
| `label` | No       | Display name shown in the menu. Defaults to the filename |
| `order` | No       | Sort weight for menu ordering. Default `50`. Lower = higher in menu |
| body    | Yes      | The system prompt sent to the LLM. Everything after the second `---` |

The **filename** (without `.md`) serves as the mode ID and must match the `mode` value in `config.json`. Use only letters, numbers, hyphens, and underscores.

> The reserved mode ID `off` disables enhancement and does not correspond to any file.

## Built-in Modes

These 3 modes are created automatically on first launch:

| File                   | Label      | Order | Description                              |
|------------------------|------------|-------|------------------------------------------|
| `proofread.md`         | 纠错润色   | 10    | Fix typos, grammar, and punctuation      |
| `translate_en.md`      | 翻译为英文 | 20    | Translate Chinese to English             |
| `commandline_master.md`| 命令行大神 | 30    | Convert natural language to shell commands|

## Add a New Mode

### Option A: From the Menu

1. Click the **VT** icon in the menu bar.
2. Go to **AI Enhance** > **Add Mode...**.
3. Edit the template in the dialog and click **Save**.
4. Enter a mode ID (e.g., `summarize`) and confirm.
5. The new mode appears in the menu immediately.

### Option B: Create a File Manually

Create a new `.md` file in the modes directory:

```bash
cat > ~/.config/VoiceText/enhance_modes/summarize.md << 'EOF'
---
label: Summarize
order: 55
---
You are a text summarization assistant.
Condense the user's input into a brief summary of 1-3 sentences.
Preserve the key information and original meaning.
Output only the summary without any explanation.
EOF
```

Restart the app to load the new mode.

### Example: Formal Email Mode

```bash
cat > ~/.config/VoiceText/enhance_modes/formal_email.md << 'EOF'
---
label: Formal Email
order: 60
---
You are a professional email writing assistant.
Rewrite the user's input as a formal, polished email body.
Use appropriate greetings and closings if context suggests an email.
Maintain the original intent and key information.
Output only the email text without any explanation.
EOF
```

### Example: Translate to Japanese

```bash
cat > ~/.config/VoiceText/enhance_modes/translate_ja.md << 'EOF'
---
label: Translate to Japanese
order: 70
---
You are a Chinese-to-Japanese translator.
Translate the user's Chinese input into natural, fluent Japanese.
Preserve the original meaning and tone.
Output only the translated text without any explanation.
EOF
```

## Edit an Existing Mode

Open the file directly with any text editor:

```bash
# Edit with your preferred editor
open -e ~/.config/VoiceText/enhance_modes/proofread.md
# or
vim ~/.config/VoiceText/enhance_modes/proofread.md
```

Changes take effect after restarting the app.

> Built-in mode files can be freely edited. VoiceText will not overwrite a file that already exists.

## Remove a Mode

Delete the corresponding `.md` file and restart:

```bash
rm ~/.config/VoiceText/enhance_modes/summarize.md
```

**Note:** If you delete a built-in mode file (e.g., `proofread.md`), it will be recreated on the next startup with default content. To permanently disable a built-in mode, replace its prompt with a passthrough instruction instead:

```markdown
---
label: (Disabled) Proofread
order: 999
---
Output the user's input exactly as-is, without any changes.
```

## Tips

- **Ordering**: Use `order` values with gaps (10, 20, 30...) so you can insert new modes between existing ones without renumbering.
- **Prompt quality**: Be specific in your prompts. Tell the LLM exactly what to do and what NOT to do. Always end with "Output only the processed text without any explanation" to avoid unwanted commentary.
- **Config compatibility**: The `mode` field in `~/.config/VoiceText/config.json` stores the mode ID (filename). If a mode file is removed but the config still references it, the app falls back to the first available mode.
- **Non-`.md` files are ignored**: You can safely keep notes (`.txt`) or backups (`.bak`) in the modes directory.

For more inspiration, see [Enhancement Mode Examples](enhance-mode-examples.md) — a collection of ready-to-use templates covering writing, translation, developer tools, and more.
