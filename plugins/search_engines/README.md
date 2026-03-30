# Search Engines

Customizable web search engines in the WenZi launcher, each with its own prefix.

## Features

### Built-in Engines

| Engine | Prefix | Description |
|--------|--------|-------------|
| Google | `g` | General web search |
| GitHub | `gh` | Repository and code search |
| X | `x` | Search posts and profiles |
| Etherscan | `eth` | Address, token, and transaction lookup |

### Actions

| Key | Action |
|-----|--------|
| `Enter` | Open search in browser |
| `Alt+Enter` | Copy search URL to clipboard |

### Help Mode

Type a prefix with no query to see the engine name, description, and available actions.

### Icon Caching

Favicons are downloaded and cached locally with a 7-day TTL for fast display.

## Configuration

Edit `engines.toml` in the plugin directory to add, remove, or modify search engines:

```toml
[[engines]]
id = "google"
name = "Google"
prefix = "g"
url = "https://www.google.com/search?q={query}"
homepage = "https://www.google.com"
subtitle = "General web search"
badge = "G"
icon_url = "https://www.google.com/favicon.ico"
```

### URL Placeholders

| Placeholder | Encoding |
|-------------|----------|
| `{query}` | URL-encoded (`urllib.parse.quote`) |
| `{query_plus}` | URL-encoded with `+` for spaces (`urllib.parse.quote_plus`) |
| `{raw}` | No encoding — raw query text |

### Engine Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Display name |
| `prefix` | Yes | Launcher activation prefix |
| `url` | Yes | Search URL template with placeholders |
| `id` | No | Identifier (defaults to lowercased name) |
| `homepage` | No | Engine homepage URL |
| `subtitle` | No | Description shown in launcher |
| `badge` | No | Short badge text next to the engine name |
| `icon_url` | No | Favicon URL (auto-generated from homepage if omitted) |

## Usage

1. Open the WenZi launcher
2. Type a prefix followed by your query (e.g., `g macOS window management`)
3. Press Enter to search in your browser, or Alt+Enter to copy the URL

## Requirements

- WenZi ≥ 0.1.12
- Internet connection
