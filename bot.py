import os
import time
import requests
from collections import defaultdict, deque

# =========================================================
# ENV
# =========================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "").strip()

TG_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
CG_BASE = "https://api.coingecko.com/api/v3"
AV_BASE = "https://www.alphavantage.co/query"

# Sticker file_id-lər (istəyə bağlı)
PUMP_STICKER = os.getenv("PUMP_STICKER", "").strip()
DUMP_STICKER = os.getenv("DUMP_STICKER", "").strip()
ON_STICKER = os.getenv("ON_STICKER", "").strip()
OFF_STICKER = os.getenv("OFF_STICKER", "").strip()

# =========================================================
# SETTINGS
# =========================================================
MAIN_LOOP_SECONDS = 20
CRYPTO_FETCH_GAP = 45
WINDOW_MINUTES = 3
ALERT_THRESHOLD = 3.0
ALERT_COOLDOWN = 300

GENERAL_LIMIT = 100
MEME_LIMIT = 100

# =========================================================
# RUNTIME STATE
# =========================================================
last_update_id = 0

# hər chat üçün modlar
chat_modes = defaultdict(lambda: {
    "meme": False,
    "newcoin": False,
})

# hər chat üçün filterlər
chat_filters = defaultdict(lambda: {
    "meme": set(),
    "newcoin": set(),
})

# price/volume history
price_history = defaultdict(lambda: deque())
volume_history = defaultdict(lambda: deque())

# eyni coin təkrar gəlməsin
sent_alerts = {}

# cache
general_cache = {"ts": 0, "data": []}
meme_cache = {"ts": 0, "data": []}

# =========================================================
# TELEGRAM HELPERS
# =========================================================
def send_message(chat_id: int, text: str) -> None:
    try:
        requests.post(
            f"{TG_BASE}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=20,
        )
    except Exception as e:
        print("send_message error:", e)

def send_sticker(chat_id: int, sticker_file_id: str) -> None:
    if not sticker_file_id:
        return
    try:
        requests.post(
            f"{TG_BASE}/sendSticker",
            data={"chat_id": chat_id, "sticker": sticker_file_id},
            timeout=20,
        )
    except Exception as e:
        print("send_sticker error:", e)

def get_updates(offset=None) -> dict:
    params = {"timeout": 60}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(f"{TG_BASE}/getUpdates", params=params, timeout=70)
    r.raise_for_status()
    return r.json()

# =========================================================
# COMMON HELPERS
# =========================================================
def now_ts() -> float:
    return time.time()

def safe_float(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default

def normalize_symbol(token: str) -> str:
    return (
        token.strip()
        .lower()
        .replace("/", "")
        .replace(",", "")
        .replace("-", "")
        .replace("_", "")
    )

def parse_symbols(parts):
    result = set()
    for part in parts:
        p = normalize_symbol(part)
        if p:
            result.add(p)
    return result

def prune_deque(dq: deque, max_age_seconds: int) -> None:
    current = now_ts()
    while dq and current - dq[0][0] > max_age_seconds:
        dq.popleft()

def update_history(asset_id: str, price: float, volume: float = 0.0) -> None:
    t = now_ts()
    price_history[asset_id].append((t, price))
    volume_history[asset_id].append((t, volume))

    max_age = WINDOW_MINUTES * 60
    prune_deque(price_history[asset_id], max_age)
    prune_deque(volume_history[asset_id], max_age)

def get_window_start_price(asset_id: str):
    dq = price_history[asset_id]
    if len(dq) < 2:
        return None
    return dq[0][1]

def calc_change_percent(old_price: float, new_price: float) -> float:
    if not old_price:
        return 0.0
    return ((new_price - old_price) / old_price) * 100.0

def get_volume_ratio(asset_id: str) -> float:
    dq = volume_history[asset_id]
    vals = [x[1] for x in dq if x[1] is not None]
    if len(vals) < 2:
        return 1.0

    current = vals[-1]
    previous = vals[:-1]
    avg = sum(previous) / max(len(previous), 1)

    if avg <= 0:
        return 1.0
    return current / avg

def should_send_alert(key: str, cooldown=ALERT_COOLDOWN) -> bool:
    t = now_ts()
    last = sent_alerts.get(key, 0)
    if t - last >= cooldown:
        sent_alerts[key] = t
        return True
    return False

def chat_filter_match(chat_id: int, mode_name: str, coin_id: str, symbol: str) -> bool:
    filt = chat_filters[chat_id][mode_name]
    if not filt:
        return True
    sid = normalize_symbol(symbol)
    cid = normalize_symbol(coin_id)
    return sid in filt or cid in filt

# =========================================================
# ALERT HELPERS
# =========================================================
def send_pump_alert(chat_id: int, title: str, name: str, symbol: str, change: float, extra: str = ""):
    send_sticker(chat_id, PUMP_STICKER)
    msg = (
        f"🟢 {title}\n"
        f"{name} ({symbol.upper()})\n"
        f"Dəyişmə: +{change:.2f}%\n"
        f"Müddət: son 3 dəqiqə"
    )
    if extra:
        msg += f"\n{extra}"
    send_message(chat_id, msg)

def send_dump_alert(chat_id: int, title: str, name: str, symbol: str, change: float, extra: str = ""):
    send_sticker(chat_id, DUMP_STICKER)
    msg = (
        f"🔴 {title}\n"
        f"{name} ({symbol.upper()})\n"
        f"Dəyişmə: {change:.2f}%\n"
        f"Müddət: son 3 dəqiqə"
    )
    if extra:
        msg += f"\n{extra}"
    send_message(chat_id, msg)

# =========================================================
# COINGECKO
# =========================================================
def cg_get(path: str, params=None):
    params = params or {}
    r = requests.get(f"{CG_BASE}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def get_general_markets_cached():
    current = now_ts()
    if current - general_cache["ts"] < CRYPTO_FETCH_GAP and general_cache["data"]:
        return general_cache["data"]

    data = cg_get(
        "/coins/markets",
        {
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": GENERAL_LIMIT,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
        },
    )
    general_cache["ts"] = current
    general_cache["data"] = data
    return data

def get_meme_markets_cached():
    current = now_ts()
    if current - meme_cache["ts"] < CRYPTO_FETCH_GAP and meme_cache["data"]:
        return meme_cache["data"]

    data = cg_get(
        "/coins/markets",
        {
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": MEME_LIMIT,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
            "category": "meme-token",
        },
    )
    meme_cache["ts"] = current
    meme_cache["data"] = data
    return data

# =========================================================
# MONITORS
# =========================================================
def check_meme_for_chat(chat_id: int):
    try:
        coins = get_meme_markets_cached()
    except Exception as e:
        print("meme error:", e)
        return

    for coin in coins:
        coin_id = coin.get("id", "")
        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "")

        if not chat_filter_match(chat_id, "meme", coin_id, symbol):
            continue

        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))

        history_key = f"meme:{coin_id}"
        update_history(history_key, price, volume)

        old_price = get_window_start_price(history_key)
        if old_price is None:
            continue

        change = calc_change_percent(old_price, price)
        vol_ratio = get_volume_ratio(history_key)

        if change >= ALERT_THRESHOLD:
            key = f"{chat_id}:meme:pump:{coin_id}"
            if should_send_alert(key):
                send_pump_alert(
                    chat_id,
                    "MEME PUMP",
                    name,
                    symbol,
                    change,
                    f"Volume ratio: x{vol_ratio:.2f}"
                )

        elif change <= -ALERT_THRESHOLD:
            key = f"{chat_id}:meme:dump:{coin_id}"
            if should_send_alert(key):
                send_dump_alert(
                    chat_id,
                    "MEME DUMP",
                    name,
                    symbol,
                    change,
                    f"Volume ratio: x{vol_ratio:.2f}"
                )

def check_newcoin_for_chat(chat_id: int):
    try:
        coins = get_general_markets_cached()
    except Exception as e:
        print("newcoin error:", e)
        return

    for coin in coins:
        coin_id = coin.get("id", "")
        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "")

        if not chat_filter_match(chat_id, "newcoin", coin_id, symbol):
            continue

        market_cap = safe_float(coin.get("market_cap"))
        market_cap_rank = coin.get("market_cap_rank")
        volume = safe_float(coin.get("total_volume"))
        price = safe_float(coin.get("current_price"))

        # Public API ilə "fresh/new" üçün praktiki məntiq
        is_fresh_candidate = (
            market_cap_rank is None
            or (isinstance(market_cap_rank, int) and market_cap_rank > 500)
            or (market_cap > 0 and market_cap < 20_000_000)
        )

        if not is_fresh_candidate:
            continue

        history_key = f"new:{coin_id}"
        update_history(history_key, price, volume)

        old_price = get_window_start_price(history_key)
        if old_price is None:
            continue

        change = calc_change_percent(old_price, price)
        vol_ratio = get_volume_ratio(history_key)

        if change >= ALERT_THRESHOLD and volume > 50_000:
            key = f"{chat_id}:newcoin:pump:{coin_id}"
            if should_send_alert(key):
                extra = (
                    f"Volume: {volume:,.0f}\n"
                    f"Volume ratio: x{vol_ratio:.2f}\n"
                    f"Market cap: {market_cap:,.0f}"
                )
                send_pump_alert(chat_id, "NEW COIN PUMP", name, symbol, change, extra)

        elif change <= -ALERT_THRESHOLD and volume > 50_000:
            key = f"{chat_id}:newcoin:dump:{coin_id}"
            if should_send_alert(key):
                extra = (
                    f"Volume: {volume:,.0f}\n"
                    f"Volume ratio: x{vol_ratio:.2f}\n"
                    f"Market cap: {market_cap:,.0f}"
                )
                send_dump_alert(chat_id, "NEW COIN DUMP", name, symbol, change, extra)

# =========================================================
# ALPHA VANTAGE SNAPSHOTS (manual)
# =========================================================
def av_get(params: dict):
    if not ALPHA_VANTAGE_KEY:
        raise Exception("ALPHA_VANTAGE_KEY yoxdur")

    query = params.copy()
    query["apikey"] = ALPHA_VANTAGE_KEY

    r = requests.get(AV_BASE, params=query, timeout=20)
    r.raise_for_status()
    data = r.json()

    if "Error Message" in data:
        raise Exception(data["Error Message"])
    if "Information" in data:
        raise Exception(data["Information"])

    return data

def forex_snapshot(chat_id: int):
    pairs = [("EUR", "USD"), ("GBP", "USD"), ("USD", "JPY")]
    lines = ["📊 Forex snapshot"]

    for a, b in pairs:
        try:
            data = av_get({
                "function": "CURRENCY_EXCHANGE_RATE",
                "from_currency": a,
                "to_currency": b,
            })
            info = data.get("Realtime Currency Exchange Rate", {})
            rate = info.get("5. Exchange Rate", "?")
            lines.append(f"{a}/{b}: {rate}")
        except Exception as e:
            lines.append(f"{a}/{b}: error")
            print("forex snapshot error:", a, b, e)

        time.sleep(12)

    send_message(chat_id, "\n".join(lines))

def birja_snapshot(chat_id: int):
    items = [
        ("WTI", "WTI Oil"),
        ("BRENT", "Brent Oil"),
        ("NATURAL_GAS", "Natural Gas"),
        ("COPPER", "Copper"),
    ]
    lines = ["🛢 Birja snapshot"]

    for fn, name in items:
        try:
            data = av_get({"function": fn, "interval": "daily"})
            arr = data.get("data", [])
            price = arr[0].get("value", "?") if arr else "?"
            lines.append(f"{name}: {price}")
        except Exception as e:
            lines.append(f"{name}: error")
            print("birja snapshot error:", fn, e)

        time.sleep(12)

    send_message(chat_id, "\n".join(lines))

# =========================================================
# COMMANDS
# =========================================================
def build_status(chat_id: int) -> str:
    lines = ["Aktiv monitorlar:"]

    for mode_name, enabled in chat_modes[chat_id].items():
        if enabled:
            flt = chat_filters[chat_id][mode_name]
            if flt:
                lines.append(f"- {mode_name}: {', '.join(sorted(flt))}")
            else:
                lines.append(f"- {mode_name}: hamısı")

    if len(lines) == 1:
        lines.append("- heç biri")

    return "\n".join(lines)

def handle_command(text: str, chat_id: int):
    text = (text or "").strip()
    lower = text.lower()

    if lower == "/id":
        send_message(chat_id, f"Chat ID: {chat_id}")
        return

    if lower == "/status":
        send_message(chat_id, build_status(chat_id))
        return

    parts = lower.split()
    cmd = parts[0]
    symbols = parse_symbols(parts[1:])

    if cmd == "/startmeme":
        chat_modes[chat_id]["meme"] = True
        if symbols:
            chat_filters[chat_id]["meme"].update(symbols)
        send_sticker(chat_id, ON_STICKER)
        if symbols:
            send_message(chat_id, f"✅ Meme başladı: {', '.join(sorted(symbols))}")
        else:
            send_message(chat_id, "✅ Meme başladı: hamısı")

    elif cmd == "/stopmeme":
        if symbols:
            chat_filters[chat_id]["meme"] -= symbols
            if not chat_filters[chat_id]["meme"]:
                chat_modes[chat_id]["meme"] = False
            send_message(chat_id, f"❌ Meme stop: {', '.join(sorted(symbols))}")
        else:
            chat_modes[chat_id]["meme"] = False
            chat_filters[chat_id]["meme"].clear()
            send_message(chat_id, "❌ Meme tam dayandı")

    elif cmd == "/startnewcoin":
        chat_modes[chat_id]["newcoin"] = True
        if symbols:
            chat_filters[chat_id]["newcoin"].update(symbols)
        send_sticker(chat_id, ON_STICKER)
        if symbols:
            send_message(chat_id, f"✅ NewCoin başladı: {', '.join(sorted(symbols))}")
        else:
            send_message(chat_id, "✅ NewCoin başladı: fresh low-cap pump candidates")

    elif cmd == "/stopnewcoin":
        if symbols:
            chat_filters[chat_id]["newcoin"] -= symbols
            if not chat_filters[chat_id]["newcoin"]:
                chat_modes[chat_id]["newcoin"] = False
            send_message(chat_id, f"❌ NewCoin stop: {', '.join(sorted(symbols))}")
        else:
            chat_modes[chat_id]["newcoin"] = False
            chat_filters[chat_id]["newcoin"].clear()
            send_message(chat_id, "❌ NewCoin tam dayandı")

    elif cmd == "/forex":
        send_message(chat_id, "⏳ Forex snapshot hazırlanır...")
        forex_snapshot(chat_id)

    elif cmd == "/birja":
        send_message(chat_id, "⏳ Birja snapshot hazırlanır...")
        birja_snapshot(chat_id)

    elif cmd == "/allstop":
        chat_modes[chat_id]["meme"] = False
        chat_modes[chat_id]["newcoin"] = False
        chat_filters[chat_id]["meme"].clear()
        chat_filters[chat_id]["newcoin"].clear()
        send_sticker(chat_id, OFF_STICKER)
        send_message(chat_id, "🛑 Bütün monitorlar dayandırıldı")

# =========================================================
# MAIN
# =========================================================
def run_monitors_for_chat(chat_id: int):
    if chat_modes[chat_id]["meme"]:
        check_meme_for_chat(chat_id)
    if chat_modes[chat_id]["newcoin"]:
        check_newcoin_for_chat(chat_id)

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN yoxdur")

    global last_update_id

    while True:
        try:
            updates = get_updates(last_update_id)

            for update in updates.get("result", []):
                last_update_id = update["update_id"] + 1

                message = update.get("message")
                if not message:
                    continue

                text = message.get("text", "")
                chat_id = message["chat"]["id"]

                if text:
                    handle_command(text, chat_id)

            for chat_id, modes in list(chat_modes.items()):
                if any(modes.values()):
                    run_monitors_for_chat(chat_id)

        except Exception as e:
            print("main error:", e)

        time.sleep(MAIN_LOOP_SECONDS)

if __name__ == "__main__":
    main()
