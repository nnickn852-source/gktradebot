import os
import time
import requests
from collections import defaultdict, deque

# =========================
# ENV
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1001234567890"))

# vergüllə ayır: 123,456
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "1251969072")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()]

TG_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
CG_BASE = "https://api.coingecko.com/api/v3"
AV_BASE = "https://www.alphavantage.co/query"

# =========================
# SETTINGS
# =========================
INTERVAL_SECONDS = 60
WINDOW_MINUTES = 3
ALERT_THRESHOLD = 3.0

# neçə coin izləsin
CRYPTO_LIMIT = 100
MEME_LIMIT = 100

# =========================
# STICKERS (öz file_id-lərinlə dəyiş)
# =========================
STICKERS = {
    "pump": "PASTE_PUMP_FROG_STICKER_FILE_ID",
    "dump": "PASTE_DUMP_FROG_STICKER_FILE_ID",
    "prepump": "PASTE_PREPUMP_FROG_STICKER_FILE_ID",
    "fake": "PASTE_FAKE_FROG_STICKER_FILE_ID",
    "smart": "PASTE_SMART_FROG_STICKER_FILE_ID",
    "on": "PASTE_ON_FROG_STICKER_FILE_ID",
    "off": "PASTE_OFF_FROG_STICKER_FILE_ID",
}

# =========================
# RUNTIME STATE
# =========================
active_modes = set()
price_history = defaultdict(lambda: deque())
volume_history = defaultdict(lambda: deque())
sent_alerts = {}
last_admin_chat_id = None

# =========================
# TELEGRAM HELPERS
# =========================
def send_message(chat_id, text):
    try:
        requests.post(
            f"{TG_BASE}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=30,
        )
    except Exception as e:
        print("send_message error:", e)

def send_sticker(chat_id, sticker_key):
    file_id = STICKERS.get(sticker_key)
    if not file_id or file_id.startswith("PASTE_"):
        return
    try:
        requests.post(
            f"{TG_BASE}/sendSticker",
            data={"chat_id": chat_id, "sticker": file_id},
            timeout=30,
        )
    except Exception as e:
        print("send_sticker error:", e)

def get_updates(offset=None):
    params = {"timeout": 60}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(f"{TG_BASE}/getUpdates", params=params, timeout=70)
    r.raise_for_status()
    return r.json()

# =========================
# COMMON HELPERS
# =========================
def now_ts():
    return time.time()

def safe_float(x, default=0.0):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default

def pct_change(old, new):
    if not old:
        return 0.0
    return ((new - old) / old) * 100.0

def prune_history(dq, max_age_seconds):
    t = now_ts()
    while dq and (t - dq[0][0] > max_age_seconds):
        dq.popleft()

def should_send_alert(alert_key, cooldown_seconds=600):
    t = now_ts()
    last = sent_alerts.get(alert_key, 0)
    if t - last >= cooldown_seconds:
        sent_alerts[alert_key] = t
        return True
    return False

def score_signal(change_pct, volume_ratio, is_new=False, is_meme=False, fake=False):
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
    return max(score, 0)

# =========================
# COINGECKO
# =========================
def cg_get(path, params=None):
    params = params or {}
    r = requests.get(f"{CG_BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def get_markets(per_page=100, category=None, order="volume_desc"):
    params = {
        "vs_currency": "usd",
        "order": order,
        "per_page": per_page,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h"
    }
    if category:
        params["category"] = category
    return cg_get("/coins/markets", params)

def get_new_coins():
    try:
        return cg_get("/coins/list/new")
    except Exception as e:
        print("get_new_coins error:", e)
        return []

# =========================
# ALPHA VANTAGE
# =========================
def av_get(params):
    if not ALPHA_VANTAGE_KEY:
        raise Exception("ALPHA_VANTAGE_KEY yoxdur")
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

def get_fx_rate(a, b):
    data = av_get({
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": a,
        "to_currency": b
    })
    info = data.get("Realtime Currency Exchange Rate", {})
    return {
        "id": f"{a}_{b}",
        "name": f"{a}/{b}",
        "symbol": f"{a}{b}",
        "price": safe_float(info.get("5. Exchange Rate")),
        "volume": 0.0,
        "kind": "forex"
    }

def get_commodity_latest(function_name, nice_name):
    data = av_get({
        "function": function_name,
        "interval": "daily"
    })
    series = data.get("data", [])
    latest = safe_float(series[0].get("value")) if len(series) >= 1 else 0.0
    return {
        "id": function_name.lower(),
        "name": nice_name,
        "symbol": function_name,
        "price": latest,
        "volume": 0.0,
        "kind": "commodity"
    }

def get_birja_assets():
    items = []

    fx_pairs = [
        ("EUR", "USD"), ("GBP", "USD"), ("USD", "JPY"), ("USD", "CHF"),
        ("AUD", "USD"), ("NZD", "USD"), ("USD", "CAD"), ("EUR", "JPY"),
        ("GBP", "JPY"), ("EUR", "GBP")
    ]
    for a, b in fx_pairs:
        try:
            items.append(get_fx_rate(a, b))
        except Exception as e:
            print("fx error:", a, b, e)

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
            print("commodity error:", fn, e)

    return items

# =========================
# DATA TRACKING
# =========================
def update_history(asset_id, price, volume=0.0):
    t = now_ts()
    price_history[asset_id].append((t, price))
    volume_history[asset_id].append((t, volume))

    prune_history(price_history[asset_id], WINDOW_MINUTES * 60)
    prune_history(volume_history[asset_id], WINDOW_MINUTES * 60)

def get_window_start_price(asset_id):
    dq = price_history[asset_id]
    if len(dq) < 2:
        return None
    return dq[0][1]

def get_volume_ratio(asset_id):
    dq = volume_history[asset_id]
    if len(dq) < 2:
        return 1.0

    vals = [x[1] for x in dq if x[1] is not None]
    if len(vals) < 2:
        return 1.0

    current = vals[-1]
    avg = sum(vals[:-1]) / max(len(vals[:-1]), 1)
    if avg <= 0:
        return 1.0
    return current / avg

# =========================
# ALERT BUILDERS
# =========================
def send_pump_alert(name, symbol, change, score, channel_id):
    send_sticker(channel_id, "pump")
    send_message(
        channel_id,
        f"🐸🚀 REAL PUMP\n"
        f"{name} ({symbol.upper()})\n"
        f"Dəyişmə: +{change:.2f}%\n"
        f"Müddət: son 3 dəqiqə\n"
        f"Score: {score}/10"
    )

def send_dump_alert(name, symbol, change, score, channel_id):
    send_sticker(channel_id, "dump")
    send_message(
        channel_id,
        f"🐸🔻 FAST DUMP\n"
        f"{name} ({symbol.upper()})\n"
        f"Dəyişmə: {change:.2f}%\n"
        f"Müddət: son 3 dəqiqə\n"
        f"Score: {score}/10"
    )

def send_prepump_alert(name, symbol, volume_ratio, channel_id):
    send_sticker(channel_id, "prepump")
    send_message(
        channel_id,
        f"🐸🧠 PRE-PUMP RADAR\n"
        f"{name} ({symbol.upper()})\n"
        f"Qiymət: hələ partlamayıb\n"
        f"Volume ratio: x{volume_ratio:.2f}\n"
        f"Status: oyanış var"
    )

def send_fake_alert(name, symbol, change, volume_ratio, channel_id):
    send_sticker(channel_id, "fake")
    send_message(
        channel_id,
        f"🐸⚠️ FAKE PUMP\n"
        f"{name} ({symbol.upper()})\n"
        f"Dəyişmə: +{change:.2f}%\n"
        f"Volume ratio: x{volume_ratio:.2f}\n"
        f"Risk: yüksək"
    )

def send_smart_alert(name, symbol, change, volume_ratio, channel_id):
    send_sticker(channel_id, "smart")
    send_message(
        channel_id,
        f"🐸💰 SMART MONEY\n"
        f"{name} ({symbol.upper()})\n"
        f"Dəyişmə: {change:+.2f}%\n"
        f"Volume ratio: x{volume_ratio:.2f}\n"
        f"Status: böyük pul girişi ehtimalı"
    )

# =========================
# MODE CHECKERS
# =========================
def check_meme_mode():
    try:
        coins = get_markets(per_page=MEME_LIMIT, category="meme-token", order="volume_desc")
    except Exception as e:
        print("meme check error:", e)
        return

    for coin in coins:
        cid = coin.get("id")
        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "").upper()
        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))

        update_history(cid, price, volume)
        old_price = get_window_start_price(cid)
        if old_price is None:
            continue

        change = pct_change(old_price, price)
        vr = get_volume_ratio(cid)

        if change >= ALERT_THRESHOLD:
            fake = vr < 1.2
            score = score_signal(change, vr, is_meme=True, fake=fake)
            if fake:
                key = f"fake_meme_{cid}"
                if should_send_alert(key):
                    send_fake_alert(name, symbol, change, vr, CHANNEL_ID)
            else:
                key = f"pump_meme_{cid}"
                if should_send_alert(key):
                    send_pump_alert(name, symbol, change, score, CHANNEL_ID)

        elif change <= -ALERT_THRESHOLD:
            key = f"dump_meme_{cid}"
            score = score_signal(change, vr, is_meme=True)
            if should_send_alert(key):
                send_dump_alert(name, symbol, change, score, CHANNEL_ID)

        elif abs(change) < 1.0 and vr >= 2.0:
            key = f"prepump_meme_{cid}"
            if should_send_alert(key):
                send_prepump_alert(name, symbol, vr, CHANNEL_ID)

        if vr >= 3.0 and abs(change) >= 1.0:
            key = f"smart_meme_{cid}"
            if should_send_alert(key):
                send_smart_alert(name, symbol, change, vr, CHANNEL_ID)

def check_newcoin_mode():
    try:
        new_list = get_new_coins()
        new_ids = {x.get("id") for x in new_list if x.get("id")}
        markets = get_markets(per_page=250, order="volume_desc")
    except Exception as e:
        print("newcoin check error:", e)
        return

    for coin in markets:
        cid = coin.get("id")
        if cid not in new_ids:
            continue

        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "").upper()
        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))

        update_history(cid, price, volume)
        old_price = get_window_start_price(cid)
        if old_price is None:
            continue

        change = pct_change(old_price, price)
        vr = get_volume_ratio(cid)

        if change >= ALERT_THRESHOLD:
            key = f"new_pump_{cid}"
            score = score_signal(change, vr, is_new=True)
            if should_send_alert(key):
                send_pump_alert(name, symbol, change, score, CHANNEL_ID)

        elif change <= -ALERT_THRESHOLD:
            key = f"new_dump_{cid}"
            score = score_signal(change, vr, is_new=True)
            if should_send_alert(key):
                send_dump_alert(name, symbol, change, score, CHANNEL_ID)

        elif abs(change) < 1.0 and vr >= 2.0:
            key = f"new_prepump_{cid}"
            if should_send_alert(key):
                send_prepump_alert(name, symbol, vr, CHANNEL_ID)

def check_prepump_mode():
    try:
        coins = get_markets(per_page=CRYPTO_LIMIT, order="volume_desc")
    except Exception as e:
        print("prepump check error:", e)
        return

    for coin in coins:
        cid = coin.get("id")
        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "").upper()
        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))

        update_history(cid, price, volume)
        old_price = get_window_start_price(cid)
        if old_price is None:
            continue

        change = pct_change(old_price, price)
        vr = get_volume_ratio(cid)

        if abs(change) < 1.0 and vr >= 2.2:
            key = f"prepump_{cid}"
            if should_send_alert(key):
                send_prepump_alert(name, symbol, vr, CHANNEL_ID)

def check_fake_mode():
    try:
        coins = get_markets(per_page=CRYPTO_LIMIT, order="volume_desc")
    except Exception as e:
        print("fake check error:", e)
        return

    for coin in coins:
        cid = coin.get("id")
        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "").upper()
        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))

        update_history(cid, price, volume)
        old_price = get_window_start_price(cid)
        if old_price is None:
            continue

        change = pct_change(old_price, price)
        vr = get_volume_ratio(cid)

        if change >= ALERT_THRESHOLD and vr < 1.2:
            key = f"fake_{cid}"
            if should_send_alert(key):
                send_fake_alert(name, symbol, change, vr, CHANNEL_ID)

def check_smart_mode():
    try:
        coins = get_markets(per_page=CRYPTO_LIMIT, order="volume_desc")
    except Exception as e:
        print("smart check error:", e)
        return

    for coin in coins:
        cid = coin.get("id")
        name = coin.get("name", "Unknown")
        symbol = coin.get("symbol", "").upper()
        price = safe_float(coin.get("current_price"))
        volume = safe_float(coin.get("total_volume"))

        update_history(cid, price, volume)
        old_price = get_window_start_price(cid)
        if old_price is None:
            continue

        change = pct_change(old_price, price)
        vr = get_volume_ratio(cid)

        if vr >= 3.0 and abs(change) >= 1.0:
            key = f"smart_{cid}"
            if should_send_alert(key):
                send_smart_alert(name, symbol, change, vr, CHANNEL_ID)

def check_birja_mode():
    try:
        items = get_birja_assets()
    except Exception as e:
        print("birja check error:", e)
        return

    for item in items:
        cid = item["id"]
        name = item["name"]
        symbol = item["symbol"]
        price = safe_float(item["price"])

        update_history(cid, price, 0.0)
        old_price = get_window_start_price(cid)
        if old_price is None:
            continue

        change = pct_change(old_price, price)
        score = score_signal(change, 1.0)

        if change >= ALERT_THRESHOLD:
            key = f"birja_pump_{cid}"
            if should_send_alert(key):
                send_pump_alert(name, symbol, change, score, CHANNEL_ID)
        elif change <= -ALERT_THRESHOLD:
            key = f"birja_dump_{cid}"
            if should_send_alert(key):
                send_dump_alert(name, symbol, change, score, CHANNEL_ID)

# =========================
# COMMANDS
# =========================
def is_admin(user_id):
    return user_id in ADMIN_IDS

def handle_command(text, user_id, chat_id):
    global active_modes, last_admin_chat_id

    last_admin_chat_id = chat_id
    text = (text or "").strip().lower()
print("TEXT =", text)
print("USER_ID =", user_id)
print("ADMIN_IDS =", ADMIN_IDS)
print("IS_ADMIN =", is_admin(user_id))
    if text == "/id":
        send_message(chat_id, f"Your user id: {user_id}")
        return

    if not is_admin(user_id):
        if text.startswith("/start"):
            send_message(chat_id, "Bu komanda yalnız admin üçündür.")
        return

    if text == "/startmeme":
        active_modes.add("meme")
        send_sticker(CHANNEL_ID, "on")
        send_message(CHANNEL_ID, "🐸 Meme radar başladı.")

    elif text == "/startbirja":
        active_modes.add("birja")
        send_sticker(CHANNEL_ID, "on")
        send_message(CHANNEL_ID, "🐸 Birja radar başladı. Pump + dump birlikdə izlənir.")

    elif text == "/startnewcoin":
        active_modes.add("newcoin")
        send_sticker(CHANNEL_ID, "on")
        send_message(CHANNEL_ID, "🐸 Yeni coin radar başladı.")

    elif text == "/startprepump":
        active_modes.add("prepump")
        send_sticker(CHANNEL_ID, "on")
        send_message(CHANNEL_ID, "🐸 Pre-pump radar başladı.")

    elif text == "/startfake":
        active_modes.add("fake")
        send_sticker(CHANNEL_ID, "on")
        send_message(CHANNEL_ID, "🐸 Fake pump radar başladı.")

    elif text == "/startsmart":
        active_modes.add("smart")
        send_sticker(CHANNEL_ID, "on")
        send_message(CHANNEL_ID, "🐸 Smart money radar başladı.")

    elif text == "/stop":
        active_modes.clear()
        send_sticker(CHANNEL_ID, "off")
        send_message(CHANNEL_ID, "🐸 Bütün monitorlar dayandırıldı.")

    elif text == "/status":
        modes = ", ".join(sorted(active_modes)) if active_modes else "heç biri"
        send_message(chat_id, f"Aktiv monitorlar: {modes}")

# =========================
# MAIN
# =========================
def run_monitors():
    if "meme" in active_modes:
        check_meme_mode()
    if "birja" in active_modes:
        check_birja_mode()
    if "newcoin" in active_modes:
        check_newcoin_mode()
    if "prepump" in active_modes:
        check_prepump_mode()
    if "fake" in active_modes:
        check_fake_mode()
    if "smart" in active_modes:
        check_smart_mode()

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

                if text:
                    handle_command(text, user_id, chat_id)

            if active_modes:
                run_monitors()

        except Exception as e:
            print("main error:", e)

        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
