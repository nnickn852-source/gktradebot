import os
import time
import requests

TOKEN = os.getenv("TELEGRAM_TOKEN")
TG = f"https://api.telegram.org/bot{TOKEN}"
CG = "https://api.coingecko.com/api/v3"

active = False
last_prices = {}
last_alert = {}
last_update = 0
last_chat = None

# ================= TELEGRAM =================
def send(chat_id, text):
    requests.post(
        f"{TG}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=20
    )

def get_updates():
    global last_update
    data = requests.get(
        f"{TG}/getUpdates",
        params={"offset": last_update + 1, "timeout": 60},
        timeout=70
    ).json()

    for u in data.get("result", []):
        last_update = u["update_id"]

        try:
            text = u["message"]["text"].lower().strip()
            chat_id = u["message"]["chat"]["id"]
            handle(text, chat_id)
        except:
            pass

# ================= COMMAND =================
def handle(text, chat_id):
    global active, last_chat
    last_chat = chat_id

    if text == "/start":
        active = True
        send(chat_id, "🚀 Yeni çıxan coin monitor başladı")

    elif text == "/stop":
        active = False
        send(chat_id, "⛔ Monitor dayandı")

# ================= COIN DATA =================
def get_coins():
    url = f"{CG}/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "volume_desc",
        "per_page": 100,
        "page": 1,
        "sparkline": "false"
    }
    return requests.get(url, params=params, timeout=20).json()

# ================= CHECK =================
def check(chat_id):
    coins = get_coins()

    for c in coins:
        name = c.get("name", "")
        symbol = c.get("symbol", "").upper()
        price = c.get("current_price", 0)
        volume = c.get("total_volume", 0)
        cap = c.get("market_cap", 0)
        rank = c.get("market_cap_rank", None)

        if not price:
            continue

        # Yeni çıxan / fresh coin namizədi kriteriyası
        # rank yoxdur və ya rank çox aşağıdadır, ya da market cap kiçikdir
        if not (rank is None or rank > 500 or cap < 20_000_000):
            continue

        if name not in last_prices:
            last_prices[name] = price
            continue

        old = last_prices[name]
        if not old:
            last_prices[name] = price
            continue

        change = ((price - old) / old) * 100

        # 5 dəqiqə cooldown
        if name in last_alert and time.time() - last_alert[name] < 300:
            last_prices[name] = price
            continue

        # 1% və üstü artım
        if change >= 1 and volume > 50_000:
            text = (
                f"🚀 NEW COIN PUMP\n\n"
                f"{name} ({symbol})\n"
                f"Artım: +{change:.2f}%\n"
                f"Qiymət: {price}\n"
                f"Həcm: {int(volume)}\n"
                f"Market Cap: {int(cap) if cap else 0}"
            )
            send(chat_id, text)
            last_alert[name] = time.time()

        last_prices[name] = price

# ================= MAIN =================
def main():
    while True:
        try:
            get_updates()

            if active and last_chat:
                check(last_chat)

        except Exception as e:
            print("ERROR:", e)

        time.sleep(15)

if __name__ == "__main__":
    main()
