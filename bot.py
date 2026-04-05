import time
import requests

# =========================
# TOKENS / KEYS
# =========================
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY")

TG_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
CG_BASE = "https://api.coingecko.com/api/v3"
AV_BASE = "https://www.alphavantage.co/query"

# =========================
# TELEGRAM
# =========================
def send_message(chat_id: int, text: str) -> None:
    requests.post(
        f"{TG_BASE}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=30,
    )

def split_text(text: str, chunk_size: int = 3500) -> list[str]:
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

def send_long_message(chat_id: int, text: str) -> None:
    for chunk in split_text(text):
        send_message(chat_id, chunk)

def get_updates(offset=None) -> dict:
    params = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(f"{TG_BASE}/getUpdates", params=params, timeout=35)
    r.raise_for_status()
    return r.json()

# =========================
# HELPERS
# =========================
def safe_float(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default

def pct_text(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"

def price_text(price: float) -> str:
    if price <= 0:
        return "?"
    if price < 1:
        return f"${price:,.8f}"
    return f"${price:,.2f}"

# =========================
# COINGECKO
# =========================
def cg_get(path: str, params=None):
    if params is None:
        params = {}
    r = requests.get(f"{CG_BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def get_crypto_markets(order: str, per_page: int = 30, category: str | None = None, page: int = 1):
    params = {
        "vs_currency": "usd",
        "order": order,
        "per_page": per_page,
        "page": page,
        "sparkline": "false",
        "price_change_percentage": "24h",
    }
    if category:
        params["category"] = category
    return cg_get("/coins/markets", params=params)

def get_new_coins():
    try:
        return cg_get("/coins/list/new")
    except Exception:
        return []

def get_new_rising_meme_coins(limit: int = 30):
    new_coins = get_new_coins()
    new_ids = {c.get("id") for c in new_coins if c.get("id")}

    # 30 üçün biraz geniş götürürük
    meme_coins = get_crypto_markets(
        order="percent_change_24h_desc",
        per_page=100,
        category="meme-token",
        page=1,
    )

    filtered = []
    for coin in meme_coins:
        coin_id = coin.get("id")
        change = safe_float(coin.get("price_change_percentage_24h"))
        if (not new_ids or coin_id in new_ids) and change > 0:
            filtered.append(coin)

    filtered.sort(
        key=lambda x: safe_float(x.get("price_change_percentage_24h")),
        reverse=True,
    )
    return filtered[:limit]

def format_crypto_list(title: str, coins: list[dict], emoji: str) -> str:
    if not coins:
        return f"{title}\nUyğun coin tapılmadı."

    lines = [title, ""]
    for i, coin in enumerate(coins[:30], start=1):
        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "").upper()
        price = safe_float(coin.get("current_price"))
        change = safe_float(coin.get("price_change_percentage_24h"))
        lines.append(f"{emoji} {i}. {name} ({symbol})")
        lines.append(f"   Qiymət: {price_text(price)}")
        lines.append(f"   24s: {pct_text(change)}")
        lines.append("")
    return "\n".join(lines)

# =========================
# ALPHA VANTAGE
# =========================
def av_get(params: dict):
    if not ALPHA_VANTAGE_KEY or ALPHA_VANTAGE_KEY == "BURAYA_ALPHA_VANTAGE_KEY_YAZ":
        raise Exception("Alpha Vantage API key əlavə edilməyib.")

    params = params.copy()
    params["apikey"] = ALPHA_VANTAGE_KEY

    r = requests.get(AV_BASE, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    if "Error Message" in data:
        raise Exception(data["Error Message"])
    if "Information" in data:
        raise Exception(data["Information"])

    return data

def get_fx_rate(from_currency: str, to_currency: str) -> dict:
    data = av_get({
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": from_currency,
        "to_currency": to_currency,
    })

    info = data.get("Realtime Currency Exchange Rate", {})
    rate = safe_float(info.get("5. Exchange Rate"))

    return {
        "name": f"{from_currency}/{to_currency}",
        "price": rate,
        "change": 0.0,   # realtime FX endpoint faiz dəyişmə vermir
        "kind": "forex",
    }

def get_gold_or_silver(symbol: str, nice_name: str) -> dict:
    data = av_get({
        "function": "GOLD_SILVER_SPOT",
        "symbol": symbol,
    })

    # cavab formatı dəyişə bildiyi üçün elastik oxuyuruq
    price = 0.0
    if isinstance(data, dict):
        for _, v in data.items():
            if isinstance(v, str):
                fv = safe_float(v, None)
                if fv is not None and fv > 0:
                    price = fv
                    break
            elif isinstance(v, dict):
                for _, vv in v.items():
                    fv = safe_float(vv, None)
                    if fv is not None and fv > 0:
                        price = fv
                        break

    return {
        "name": nice_name,
        "price": price,
        "change": 0.0,
        "kind": "metal",
    }

def get_commodity_latest(function_name: str, nice_name: str) -> dict:
    data = av_get({
        "function": function_name,
        "interval": "daily",
    })

    series = data.get("data", [])
    latest = 0.0
    prev = 0.0

    if isinstance(series, list) and len(series) >= 1:
        latest = safe_float(series[0].get("value"))
    if isinstance(series, list) and len(series) >= 2:
        prev = safe_float(series[1].get("value"), latest)

    change = 0.0
    if prev:
        change = ((latest - prev) / prev) * 100

    return {
        "name": nice_name,
        "price": latest,
        "change": change,
        "kind": "commodity",
    }

def get_real_birja_items() -> list[dict]:
    items = []

    fx_pairs = [
        ("EUR", "USD"),
        ("GBP", "USD"),
        ("USD", "JPY"),
        ("USD", "CHF"),
        ("AUD", "USD"),
        ("NZD", "USD"),
        ("USD", "CAD"),
        ("EUR", "JPY"),
        ("GBP", "JPY"),
        ("EUR", "GBP"),
    ]

    for a, b in fx_pairs:
        try:
            items.append(get_fx_rate(a, b))
        except Exception as e:
            print("FX xəta:", a, b, e)

    try:
        items.append(get_gold_or_silver("GOLD", "Gold (XAU/USD)"))
    except Exception as e:
        print("Gold xəta:", e)

    try:
        items.append(get_gold_or_silver("SILVER", "Silver (XAG/USD)"))
    except Exception as e:
        print("Silver xəta:", e)

    commodity_map = [
        ("WTI", "WTI Oil"),
        ("BRENT", "Brent Oil"),
        ("NATURAL_GAS", "Natural Gas"),
        ("COPPER", "Copper"),
    ]

    for fn, name in commodity_map:
        try:
            items.append(get_commodity_latest(fn, name))
        except Exception as e:
            print("Commodity xəta:", fn, e)

    return items

def format_birja_list(title: str, items: list[dict], rising: bool) -> str:
    if not items:
        return f"{title}\nMəlumat tapılmadı."

    # FX endpoint faiz dəyişmə vermədiyi üçün 0 ola bilər
    sorted_items = sorted(items, key=lambda x: safe_float(x.get("change")), reverse=rising)

    lines = [title, ""]
    count = 0

    for item in sorted_items:
        change = safe_float(item.get("change"))
        price = safe_float(item.get("price"))
        emoji = "🟢" if change >= 0 else "🔴"

        # commodity-lərdə rising/dump filtrini tətbiq edirik
        # FX/metallarda change 0 gələ bilər, onları da siyahıda saxlayırıq
        if item.get("kind") == "commodity":
            if rising and change <= 0:
                continue
            if not rising and change >= 0:
                continue

        count += 1
        lines.append(f"{emoji} {count}. {item.get('name', 'Unknown')}")
        lines.append(f"   Qiymət: {price_text(price)}")
        lines.append(f"   Dəyişmə: {pct_text(change)}")
        lines.append("")

        if count >= 30:
            break

    if count == 0:
        lines.append("Uyğun alət tapılmadı.")

    lines.append("Qeyd: Forex realtime məzənnə kimi gəlir; commodities üçün dəyişmə faizini son iki günlük dəyərdən hesablayırıq.")
    return "\n".join(lines)

# =========================
# COMMANDS
# =========================
def handle_message(chat_id: int, text: str) -> None:
    text = text.strip().lower()

    if text == "/start":
        send_message(
            chat_id,
            "📊 Komandalar:\n"
            "/pump - 30 qalxan coin\n"
            "/dump - 30 düşən coin\n"
            "/meme - 30 meme coin\n"
            "/newcoin - yeni çıxmış qalxan meme coinlər\n"
            "/birjapump - forex / metal / neft / commodities\n"
            "/birjadump - forex / metal / neft / commodities",
        )

    elif text == "/pump":
        coins = get_crypto_markets(order="percent_change_24h_desc", per_page=30)
        send_long_message(chat_id, format_crypto_list("📈 PUMP COINLƏR (30)", coins, "🟢"))

    elif text == "/dump":
        coins = get_crypto_markets(order="percent_change_24h_asc", per_page=30)
        send_long_message(chat_id, format_crypto_list("📉 DUMP COINLƏR (30)", coins, "🔴"))

    elif text == "/meme":
        coins = get_crypto_markets(
            order="percent_change_24h_desc",
            per_page=30,
            category="meme-token",
        )
        send_long_message(chat_id, format_crypto_list("🔥 MEME COINLƏR (30)", coins, "🟡"))

    elif text == "/newcoin":
        coins = get_new_rising_meme_coins(limit=30)
        send_long_message(chat_id, format_crypto_list("🚀 YENİ ÇIXMIŞ QALXAN MEME COINLƏR (30)", coins, "🆕"))

    elif text == "/birjapump":
        try:
            items = get_real_birja_items()
            send_long_message(chat_id, format_birja_list("📊 BİRJA PUMP (30)", items, rising=True))
        except Exception as e:
            send_message(chat_id, f"Birja bölməsi işləmədi: {e}")

    elif text == "/birjadump":
        try:
            items = get_real_birja_items()
            send_long_message(chat_id, format_birja_list("📊 BİRJA DUMP (30)", items, rising=False))
        except Exception as e:
            send_message(chat_id, f"Birja bölməsi işləmədi: {e}")

    else:
        send_message(
            chat_id,
            "Komandanı düzgün yaz:\n"
            "/start\n"
            "/pump\n"
            "/dump\n"
            "/meme\n"
            "/newcoin\n"
            "/birjapump\n"
            "/birjadump"
        )

# =========================
# MAIN
# =========================
def main():
    offset = None

    while True:
        try:
            updates = get_updates(offset)

            for update in updates.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message")
                if not message:
                    continue

                chat_id = message["chat"]["id"]
                text = message.get("text", "")
                if not text:
                    continue

                handle_message(chat_id, text)

        except Exception as e:
            print("Xəta:", e)

        time.sleep(1)

if __name__ == "__main__":
    main()
