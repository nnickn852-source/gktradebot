```python
import os
import time
import requests
from collections import defaultdict, deque

# =========================================================
# ENV VARIABLES
# =========================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "").strip()

# Kanal ID nümunəsi: -1001234567890
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1001234567890"))

# Admin ID-lər nümunəsi: 123456789,987654321
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()]

# Sticker file_id-lər. İstəyə bağlıdır.
PUMP_STICKER = os.getenv("PUMP_STICKER", "").strip()
DUMP_STICKER = os.getenv("DUMP_STICKER", "").strip()
PREPUMP_STICKER = os.getenv("PREPUMP_STICKER", "").strip()
FAKE_STICKER = os.getenv("FAKE_STICKER", "").strip()
SMART_STICKER = os.getenv("SMART_STICKER", "").strip()
ON_STICKER = os.getenv("ON_STICKER", "").strip()
OFF_STICKER = os.getenv("OFF_STICKER", "").strip()

# =========================================================
# CONSTANTS
# =========================================================
TG_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
CG_BASE = "https://api.coingecko.com/api/v3"
AV_BASE = "https://www.alphavantage.co/query"

# Bloka düşməmək üçün balanslı interval
MAIN_LOOP_SECONDS = 25
CRYPTO_MIN_GAP = 25
BIRJA_MIN_GAP = 70

WINDOW_MINUTES = 3
ALERT_THRESHOLD = 3.0

GENERAL_LIMIT = 100
MEME_LIMIT = 100

ALERT_COOLDOWN = 600  # eyni asset üçün 10 dəqiqə təkrar atmasın

# =========================================================
# RUNTIME STATE
# =========================================================
active_modes = {
    "meme": False,
    "birja": False,
    "newcoin": False,
    "prepump": False,
    "fake": False,
    "smart": False,
}

# Hər mod üçün seçilmiş symbol/id-lər. Boşdursa hamısı.
tracked_symbols = {
    "meme": set(),
    "birja": set(),
    "newcoin": set(),
    "prepump": set(),
    "fake": set(),
    "smart": set(),
}

price_history = defaultdict(lambda: deque())
volume_history = defaultdict(lambda: deque())
sent_alerts = {}

last_run_at = {
    "meme": 0,
    "birja": 0,
    "newcoin": 0,
    "prepump": 0,
    "fake": 0,
    "smart": 0,
}

general_market_cache = {"ts": 0, "data": []}
meme_market_cache = {"ts": 0, "data": []}
new_coins_cache = {"ts": 0, "data": []}

# =========================================================
# TELEGRAM HELPERS
# =========================================================
def send_message(chat_id: int, text: str) -> None:
    try:
        requests.post(
            f"{TG_BASE}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=25,
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
            timeout=25,
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

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def normalize_symbol(token: str) -> str:
    return token.strip().lower().replace("/", "").replace(",", "").replace("-", "").replace("_", "")

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

def score_signal(change_pct: float, volume_ratio: float, is_new=False, is_meme=False, fake=False) -> int:
    score = 0
    if abs(change_pct) >= 3:
        score += 2
    if abs(change_pct) >= 5:
        score += 1
    if volume_ratio >= 1.5:
        score += 2
    if volume_ratio >= 2.5:
        score += 1
    if is_new:
        score += 2
    if is_meme:
        score += 1
    if fake:
        score -= 3
    if score < 0:
        score = 0
    if score > 10:
        score = 10
    return score

def matches_filter(mode_name: str, coin_id: str, symbol: str) -> bool:
    filt = tracked_symbols.get(mode_name, set())
    if not filt:
        return True
    sid = normalize_symbol(symbol)
    cid = normalize_symbol(coin_id)
    return sid in filt or cid in filt

# =========================================================
# ALERT SENDERS
# =========================================================
def send_pump_alert(name: str, symbol: str, change: float, score: int):
    send_sticker(CHANNEL_ID, PUMP_STICKER)
    send_message(
        CHANNEL_ID,
        f"🐸🚀 REAL PUMP\n"
        f"{name} ({symbol.upper()})\n"
        f"Dəyişmə: +{change:.2f}%\n"
        f"Müddət: son 3 dəqiqə\n"
        f"Score: {score}/10"
    )

def send_dump_alert(name: str, symbol: str, change: float, score: int):
    send_sticker(CHANNEL_ID, DUMP_STICKER)
    send_message(
        CHANNEL_ID,
        f"🐸🔻 FAST DUMP\n"
        f"{name} ({symbol.upper()})\n"
        f"Dəyişmə: {change:.2f}%\n"
        f"Müddət: son 3 dəqiqə\n"
        f"Score: {score}/10"
    )

def send_prepump_alert(name: str, symbol: str, volume_ratio: float):
    send_sticker(CHANNEL_ID, PREPUMP_STICKER)
    send_message(
        CHANNEL_ID,
        f"🐸🧠 PRE-PUMP RADAR\n"
        f"{name} ({symbol.upper()})\n"
        f"Qiymət: hələ partlamayıb\n"
        f"Volume ratio: x{volume_ratio:.2f}\n"
        f"Status: oyanış var"
    )

def send_fake_alert(name: str, symbol: str, change: float, volume_ratio: float):
    send_sticker(CHANNEL_ID, FAKE_STICKER)
    send_message(
        CHANNEL_ID,
        f"🐸⚠️ FAKE PUMP\n"
        f"{name} ({symbol.upper()})\n"
        f"Dəyişmə: +{change:.2f}%\n"
        f"Volume ratio: x{volume_ratio:.2f}\n"
        f"Risk: yüksək"
    )

def send_smart_alert(name: str, symbol: str, change: float, volume_ratio: float):
    send_sticker(CHANNEL_ID, SMART_STICKER)
    send_message(
        CHANNEL_ID,
        f"🐸💰 SMART MONEY\n"
        f"{name} ({symbol.upper()})\n"
        f"Dəyişmə: {change:+.2f}%\n"
        f"Volume ratio: x{volume_ratio:.2f}\n"
        f"Status: böyük pul girişi ehtimalı"
    )

# =========================================================
# COINGECKO
# =========================================================
def cg_get(path: str, params=None):
    if params is None:
        params = {}
    r = requests.get(f"{CG_BASE}{path}", params=params, timeout=25)
    r.raise_for_status()
    return r.json()

def get_general_markets_cached():
    current = now_ts()
    if current - general_market_cache["ts"] < CRYPTO_MIN_GAP and general_market_cache["data"]:
        return general_market_cache["data"]

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
    general_market_cache["ts"] = current
    general_market_cache["data"] = data
    return data

def get_meme_markets_cached():
    current = now_ts()
    if current - meme_market_cache["ts"] < CRYPTO_MIN_GAP and meme_market_cache["data"]:
        return meme_market_cache["data"]

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
    meme_market_cache["ts"] = current
    meme_market_cache["data"] = data
    return data

def get_new_coins_cached():
    current = now_ts()
    if current - new_coins_cache["ts"] < 180 and new_coins_cache["data"]:
        return new_coins_cache["data"]

    try:
        data = cg_get("/coins/list/new")
    except Exception:
        data = []

    new_coins_cache["ts"] = current
    new_coins_cache["data"] = data
    return data

# =========================================================
# ALPHA VANTAGE / BIRJA
# =========================================================
def av_get(params: dict):
    if not ALPHA_VANTAGE_KEY:
        raise Exception("ALPHA_VANTAGE_KEY yoxdur")

    query = params.copy()
    query["apikey"] = ALPHA_VANTAGE_KEY

    r = requests.get(AV_BASE, params=query, timeout=25)
    r.raise_for_status()
    data = r.json()

    if "Error Message" in data:
        raise Exception(data["Error Message"])
    if "Information" in data:
        raise Exception(data["Information"])

    return data

def get_fx_rate(from_currency: str, to_currency: str) -> dict:
    data = av_get(
        {
            "function": "CURRENCY_EXCHANGE_RATE",
            "from_currency": from_currency,
            "to_currency": to_currency,
        }
    )
    info = data.get("Realtime Currency Exchange Rate", {})
    rate = safe_float(info.get("5. Exchange Rate"))
    return {
        "id": f"{from_currency.lower()}{to_currency.lower()}",
        "name": f"{from_currency}/{to_currency}",
        "symbol": f"{from_currency}{to_currency}".lower(),
        "price": rate,
        "volume": 0.0,
    }

def get_commodity_latest(function_name: str, nice_name: str) -> dict:
    data = av_get(
        {
            "function": function_name,
            "interval": "daily",
        }
    )
    series = data.get("data", [])
    latest = safe_float(series[0].get("value")) if len(series) >= 1 else 0.0
    return {
        "id": function_name.lower(),
        "name": nice_name,
        "symbol": function_name.lower(),
        "price": latest,
        "volume": 0.0,
    }

def get_birja_assets():
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
            print("FX error:", a, b, e)

    commodities = [
        ("WTI", "WTI Oil"),
        ("BRENT", "Brent Oil"),
        ("NATURAL_GAS", "Natural Gas"),
        ("COPPER", "Copper"),
    ]

    for fn, name in commodities:
        try:
            items.append(get_commodity_latest(fn, name))
        except Exception as e:
            print("Commodity error:", fn, e)

    return items

# =========================================================
# MODE CHECKERS
# =========================================================
def check_meme_mode():
    if now_ts() - last_run_at["meme"] < CRYPTO_MIN_GAP:
        return
    last_run_at["meme"] = now_ts()

    try:
        coins = get_meme_markets_cached()
    except Exception as e:
        print("meme error:", e)
        return

    for coin in coins:
        coin_id = coin.get("id", "")
        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "")
        if not matches_filter("meme", coin_id, symbol):
            continue

        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))

        update_history(coin_id, price, volume)
        old_price = get_window_start_price(coin_id)
        if old_price is None:
            continue

        change = calc_change_percent(old_price, price)
        vr = get_volume_ratio(coin_id)

        if change >= ALERT_THRESHOLD:
            fake = vr < 1.2
            if fake:
                key = f"fake_meme_{coin_id}"
                if should_send_alert(key):
                    send_fake_alert(name, symbol, change, vr)
            else:
                key = f"pump_meme_{coin_id}"
                score = score_signal(change, vr, is_meme=True)
                if should_send_alert(key):
                    send_pump_alert(name, symbol, change, score)

        elif change <= -ALERT_THRESHOLD:
            key = f"dump_meme_{coin_id}"
            score = score_signal(change, vr, is_meme=True)
            if should_send_alert(key):
                send_dump_alert(name, symbol, change, score)

def check_newcoin_mode():
    if now_ts() - last_run_at["newcoin"] < CRYPTO_MIN_GAP:
        return
    last_run_at["newcoin"] = now_ts()

    try:
        new_list = get_new_coins_cached()
        new_ids = {x.get("id") for x in new_list if x.get("id")}
        markets = get_general_markets_cached()
    except Exception as e:
        print("newcoin error:", e)
        return

    for coin in markets:
        coin_id = coin.get("id", "")
        if coin_id not in new_ids:
            continue

        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "")
        if not matches_filter("newcoin", coin_id, symbol):
            continue

        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))

        update_history(coin_id, price, volume)
        old_price = get_window_start_price(coin_id)
        if old_price is None:
            continue

        change = calc_change_percent(old_price, price)
        vr = get_volume_ratio(coin_id)

        if change >= ALERT_THRESHOLD:
            key = f"new_pump_{coin_id}"
            score = score_signal(change, vr, is_new=True)
            if should_send_alert(key):
                send_pump_alert(name, symbol, change, score)

        elif change <= -ALERT_THRESHOLD:
            key = f"new_dump_{coin_id}"
            score = score_signal(change, vr, is_new=True)
            if should_send_alert(key):
                send_dump_alert(name, symbol, change, score)

def check_prepump_mode():
    if now_ts() - last_run_at["prepump"] < CRYPTO_MIN_GAP:
        return
    last_run_at["prepump"] = now_ts()

    try:
        coins = get_general_markets_cached()
    except Exception as e:
        print("prepump error:", e)
        return

    for coin in coins:
        coin_id = coin.get("id", "")
        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "")
        if not matches_filter("prepump", coin_id, symbol):
            continue

        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))

        update_history(coin_id, price, volume)
        old_price = get_window_start_price(coin_id)
        if old_price is None:
            continue

        change = calc_change_percent(old_price, price)
        vr = get_volume_ratio(coin_id)

        if abs(change) < 1.0 and vr >= 2.2:
            key = f"prepump_{coin_id}"
            if should_send_alert(key):
                send_prepump_alert(name, symbol, vr)

def check_fake_mode():
    if now_ts() - last_run_at["fake"] < CRYPTO_MIN_GAP:
        return
    last_run_at["fake"] = now_ts()

    try:
        coins = get_general_markets_cached()
    except Exception as e:
        print("fake error:", e)
        return

    for coin in coins:
        coin_id = coin.get("id", "")
        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "")
        if not matches_filter("fake", coin_id, symbol):
            continue

        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))

        update_history(coin_id, price, volume)
        old_price = get_window_start_price(coin_id)
        if old_price is None:
            continue

        change = calc_change_percent(old_price, price)
        vr = get_volume_ratio(coin_id)

        if change >= ALERT_THRESHOLD and vr < 1.2:
            key = f"fake_{coin_id}"
            if should_send_alert(key):
                send_fake_alert(name, symbol, change, vr)

def check_smart_mode():
    if now_ts() - last_run_at["smart"] < CRYPTO_MIN_GAP:
        return
    last_run_at["smart"] = now_ts()

    try:
        coins = get_general_markets_cached()
    except Exception as e:
        print("smart error:", e)
        return

    for coin in coins:
        coin_id = coin.get("id", "")
        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "")
        if not matches_filter("smart", coin_id, symbol):
            continue

        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))

        update_history(coin_id, price, volume)
        old_price = get_window_start_price(coin_id)
        if old_price is None:
            continue

        change = calc_change_percent(old_price, price)
        vr = get_volume_ratio(coin_id)

        if vr >= 3.0 and abs(change) >= 1.0:
            key = f"smart_{coin_id}"
            if should_send_alert(key):
                send_smart_alert(name, symbol, change, vr)

def check_birja_mode():
    if now_ts() - last_run_at["birja"] < BIRJA_MIN_GAP:
        return
    last_run_at["birja"] = now_ts()

    try:
        items = get_birja_assets()
    except Exception as e:
        print("birja error:", e)
        return

    for item in items:
        asset_id = item["id"]
        name = item["name"]
        symbol = item["symbol"]
        if not matches_filter("birja", asset_id, symbol):
            continue

        price = safe_float(item["price"])
        update_history(asset_id, price, 0.0)

        old_price = get_window_start_price(asset_id)
        if old_price is None:
            continue

        change = calc_change_percent(old_price, price)
        score = score_signal(change, 1.0)

        if change >= ALERT_THRESHOLD:
            key = f"birja_pump_{asset_id}"
            if should_send_alert(key):
                send_pump_alert(name, symbol, change, score)

        elif change <= -ALERT_THRESHOLD:
            key = f"birja_dump_{asset_id}"
            if should_send_alert(key):
                send_dump_alert(name, symbol, change, score)

# =========================================================
# COMMANDS
# =========================================================
def mode_label(mode_name: str) -> str:
    labels = {
        "meme": "Meme",
        "birja": "Birja",
        "newcoin": "NewCoin",
        "prepump": "PrePump",
        "fake": "Fake",
        "smart": "Smart",
    }
    return labels.get(mode_name, mode_name)

def start_mode(mode_name: str, symbols: set):
    active_modes[mode_name] = True
    if symbols:
        tracked_symbols[mode_name].update(symbols)

def stop_mode(mode_name: str, symbols: set):
    if symbols:
        tracked_symbols[mode_name] -= symbols
        if not tracked_symbols[mode_name]:
            active_modes[mode_name] = False
    else:
        active_modes[mode_name] = False
        tracked_symbols[mode_name].clear()

def build_status_text():
    lines = ["Aktiv monitorlar:"]
    any_active = False

    for mode_name, enabled in active_modes.items():
        if enabled:
            any_active = True
            flt = tracked_symbols[mode_name]
            if flt:
                lines.append(f"- {mode_label(mode_name)}: {', '.join(sorted(flt))}")
            else:
                lines.append(f"- {mode_label(mode_name)}: hamısı")

    if not any_active:
        lines.append("- heç biri")

    return "\n".join(lines)

def handle_command(text: str, user_id: int, chat_id: int):
    text = (text or "").strip()
    lower = text.lower()

    if lower == "/id":
        send_message(chat_id, f"Your user id: {user_id}")
        return

    if lower == "/status":
        send_message(chat_id, build_status_text())
        return

    if not is_admin(user_id):
        if lower.startswith("/start") or lower.startswith("/stop") or lower == "/allstop":
            send_message(chat_id, "Bu komanda yalnız admin üçündür.")
        return

    parts = lower.split()
    cmd = parts[0]
    symbols = parse_symbols(parts[1:])

    if cmd == "/startmeme":
        start_mode("meme", symbols)
        send_sticker(CHANNEL_ID, ON_STICKER)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 Meme radar başladı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 Meme radar başladı: hamısı")

    elif cmd == "/stopmeme":
        stop_mode("meme", symbols)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 Meme radar dayandırıldı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 Meme radar tam dayandırıldı")

    elif cmd == "/startbirja":
        start_mode("birja", symbols)
        send_sticker(CHANNEL_ID, ON_STICKER)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 Birja radar başladı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 Birja radar başladı: hamısı")

    elif cmd == "/stopbirja":
        stop_mode("birja", symbols)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 Birja radar dayandırıldı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 Birja radar tam dayandırıldı")

    elif cmd == "/startnewcoin":
        start_mode("newcoin", symbols)
        send_sticker(CHANNEL_ID, ON_STICKER)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 NewCoin radar başladı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 NewCoin radar başladı: hamısı")

    elif cmd == "/stopnewcoin":
        stop_mode("newcoin", symbols)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 NewCoin radar dayandırıldı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 NewCoin radar tam dayandırıldı")

    elif cmd == "/startprepump":
        start_mode("prepump", symbols)
        send_sticker(CHANNEL_ID, ON_STICKER)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 PrePump radar başladı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 PrePump radar başladı: hamısı")

    elif cmd == "/stopprepump":
        stop_mode("prepump", symbols)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 PrePump radar dayandırıldı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 PrePump radar tam dayandırıldı")

    elif cmd == "/startfake":
        start_mode("fake", symbols)
        send_sticker(CHANNEL_ID, ON_STICKER)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 Fake radar başladı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 Fake radar başladı: hamısı")

    elif cmd == "/stopfake":
        stop_mode("fake", symbols)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 Fake radar dayandırıldı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 Fake radar tam dayandırıldı")

    elif cmd == "/startsmart":
        start_mode("smart", symbols)
        send_sticker(CHANNEL_ID, ON_STICKER)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 Smart radar başladı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 Smart radar başladı: hamısı")

    elif cmd == "/stopsmart":
        stop_mode("smart", symbols)
        if symbols:
            send_message(CHANNEL_ID, f"🐸 Smart radar dayandırıldı: {', '.join(sorted(symbols))}")
        else:
            send_message(CHANNEL_ID, "🐸 Smart radar tam dayandırıldı")

    elif cmd == "/allstop":
        for mode_name in active_modes.keys():
            active_modes[mode_name] = False
            tracked_symbols[mode_name].clear()
        send_sticker(CHANNEL_ID, OFF_STICKER)
        send_message(CHANNEL_ID, "🐸 Bütün monitorlar dayandırıldı")

# =========================================================
# RUNNERS
# =========================================================
def run_monitors():
    if active_modes["meme"]:
        check_meme_mode()
    if active_modes["birja"]:
        check_birja_mode()
    if active_modes["newcoin"]:
        check_newcoin_mode()
    if active_modes["prepump"]:
        check_prepump_mode()
    if active_modes["fake"]:
        check_fake_mode()
    if active_modes["smart"]:
        check_smart_mode()

# =========================================================
# MAIN
# =========================================================
def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN yoxdur")

    offset = None

    while True:
        try:
            updates = get_updates(offset)

            for update in updates.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message")
                if not message:
                    continue

                text = message.get("text", "")
                user = message.get("from", {})
                user_id = user.get("id")
                chat_id = message["chat"]["id"]

                if text and user_id is not None:
                    handle_command(text, user_id, chat_id)

            if any(active_modes.values()):
                run_monitors()

        except Exception as e:
            print("main error:", e)

        time.sleep(MAIN_LOOP_SECONDS)

if __name__ == "__main__":
    main()
```
