"""Crypto Prices — live cryptocurrency prices in the menubar.

Fetches prices from Binance Futures API every 5 seconds and displays
BTC price in the menubar with a dropdown showing all tracked symbols.
"""

from __future__ import annotations

import json
import urllib.request

_PRICE_SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "LINKUSDT"]


def _format_price(price):
    if price >= 10000:
        return f"{price:.0f}"
    elif price >= 1000:
        return f"{price:.1f}"
    elif price >= 1:
        return f"{price:.2f}"
    return f"{price:.4f}"


def setup(wz) -> None:
    """Entry point called by the plugin loader."""
    price_bar = wz.menubar.create("prices", title="--")

    def _fetch_prices():
        try:
            with urllib.request.urlopen(
                "https://fapi.binance.com/fapi/v2/ticker/price", timeout=10
            ) as resp:
                data = json.loads(resp.read())
        except Exception:
            price_bar.set_title("--")
            return

        price_map = {}
        sym_set = set(_PRICE_SYMS)
        for item in data:
            if item["symbol"] in sym_set:
                price_map[item["symbol"]] = float(item["price"])

        if "BTCUSDT" in price_map:
            price_bar.set_title(_format_price(price_map["BTCUSDT"]))

        menu_items = []
        for sym in _PRICE_SYMS:
            label = sym.replace("USDT", "")
            price = price_map.get(sym)
            price_str = _format_price(price) if price else "--"
            url = f"https://www.binance.com/en/trade/{label}_USDT?layout=pro"
            menu_items.append({
                "title": f"{label}  {price_str}",
                "action": lambda u=url: wz.execute(f"open '{u}'", background=True),
            })
        price_bar.set_menu(menu_items)

    wz.timer.every(5.0, _fetch_prices)
