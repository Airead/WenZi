# Crypto Prices

Live cryptocurrency prices in the menubar via Binance Futures API.

## Features

### Menubar Display

Shows the current BTC price directly in the macOS menubar, updated every 5 seconds. Click to expand a dropdown with prices for all tracked cryptocurrencies.

### Tracked Symbols

| Symbol | Pair |
|--------|------|
| BTC | BTCUSDT |
| ETH | ETHUSDT |
| BNB | BNBUSDT |
| SOL | SOLUSDT |
| LINK | LINKUSDT |

### Smart Price Formatting

Prices are displayed with appropriate precision based on value:
- ≥ 10,000 → no decimals (e.g., `84521`)
- ≥ 1,000 → 1 decimal (e.g., `1843.2`)
- ≥ 1 → 2 decimals (e.g., `13.45`)
- < 1 → 4 decimals (e.g., `0.0042`)

### Quick Trade Access

Click any symbol in the dropdown to open its Binance trading page (Pro layout) in your default browser.

## Usage

Once installed and enabled, the plugin automatically starts fetching prices. The BTC price appears in the menubar; click it to see all tracked symbols.

If the API is unreachable, the menubar shows `--` until the next successful fetch.

## Requirements

- WenZi ≥ 0.1.12
- Internet connection (Binance Futures API)
