import time
import requests
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY")

TG_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
CG_URL = "https://api.coingecko.com/api/v3"

active_modes = {
    "meme": False,
    "new": False,
}

last_prices = {}
last_alert = {}

# =========================
# TELEGRAM
# =========================
def send(chat_id, text):
    requests.post(f"{TG_URL}/sendMessage", data={
        "chat_id": chat_id,
        "text": text
    })


# =========================
# COINGECKO
# =========================
def get_meme():
    try:
        url = f"{CG_URL}/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": 50,
            "page": 1,
            "price_change_percentage": "1h",
            "category": "meme-token"
        }
        return requests.get(url, params=params).json()
    except:
        return []

def get_new():
    try:
        url = f"{CG_URL}/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 50,
            "page": 1,
            "price_change_percentage": "1h"
        }
        return requests.get(url, params=params).json()
    except:
        return []


# =========================
# ALERT CHECK
# =========================
def check(data, chat_id):
    for coin in data:
        name = coin["name"]
        price = coin["current_price"]

        if name not in last_prices:
            last_prices[name] = price
            continue

        old = last_prices[name]
        change = ((price - old) / old) * 100

        # cooldown 5 dəq
        if name in last_alert:
            if time.time() - last_alert[name] < 300:
                continue

        if abs(change) >= 3:
            emoji = "🟢" if change > 0 else "🔴"

            text = f"{emoji} {name}\n{change:.2f}% dəyişdi\nQiymət: {price}"
            send(chat_id, text)

            last_alert[name] = time.time()

        last_prices[name] = price


# =========================
# FOREX / BIRJA (MANUAL)
# =========================
def forex(chat_id):
    pairs = ["EURUSD", "USDJPY", "GBPUSD"]

    text = "📊 Forex:\n"
    for p in pairs:
        url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency={p[:3]}&to_currency={p[3:]}&apikey={ALPHA_VANTAGE_KEY}"
        r = requests.get(url).json()

        try:
            rate = r["Realtime Currency Exchange Rate"]["5. Exchange Rate"]
            text += f"{p}: {rate}\n"
        except:
            text += f"{p}: error\n"

        time.sleep(12)  # limit üçün

    send(chat_id, text)


def birja(chat_id):
    items = {
        "GOLD": "XAU",
        "SILVER": "XAG",
        "OIL": "WTI"
    }

    text = "🛢 Birja:\n"

    for name, sym in items.items():
        url = f"https://www.alphavantage.co/query?function=COMMODITY_EXCHANGE_RATE&symbol={sym}&apikey={ALPHA_VANTAGE_KEY}"
        r = requests.get(url).json()

        try:
            price = r["data"][0]["value"]
            text += f"{name}: {price}\n"
        except:
            text += f"{name}: error\n"

        time.sleep(12)

    send(chat_id, text)


# =========================
# COMMAND
# =========================
def handle(text, chat_id):
    global active_modes

    if text == "/startmeme":
        active_modes["meme"] = True
        send(chat_id, "✅ Meme başladı")

    elif text == "/stopmeme":
        active_modes["meme"] = False
        send(chat_id, "❌ Meme dayandı")

    elif text == "/startnewcoin":
        active_modes["new"] = True
        send(chat_id, "✅ New coin başladı")

    elif text == "/stopnewcoin":
        active_modes["new"] = False
        send(chat_id, "❌ New coin dayandı")

    elif text == "/forex":
        forex(chat_id)

    elif text == "/birja":
        birja(chat_id)

    elif text == "/allstop":
        active_modes["meme"] = False
        active_modes["new"] = False
        send(chat_id, "🛑 Hamısı dayandı")


# =========================
# TELEGRAM POLLING
# =========================
last_update = 0

def get_updates():
    global last_update
    url = f"{TG_URL}/getUpdates?offset={last_update+1}"
    data = requests.get(url).json()

    for u in data["result"]:
        last_update = u["update_id"]

        try:
            text = u["message"]["text"]
            chat_id = u["message"]["chat"]["id"]
            handle(text.lower(), chat_id)
        except:
            pass


# =========================
# MAIN LOOP
# =========================
def main():
    while True:
        try:
            get_updates()

            if active_modes["meme"]:
                data = get_meme()
                check(data, last_chat)

            if active_modes["new"]:
                data = get_new()
                check(data, last_chat)

        except Exception as e:
            print("ERROR:", e)

        time.sleep(10)


last_chat = None

def handle(text, chat_id):
    global active_modes, last_chat
    last_chat = chat_id

    if text == "/startmeme":
        active_modes["meme"] = True
        send(chat_id, "✅ Meme başladı")

    elif text == "/stopmeme":
        active_modes["meme"] = False
        send(chat_id, "❌ Meme dayandı")

    elif text == "/startnewcoin":
        active_modes["new"] = True
        send(chat_id, "✅ New başladı")

    elif text == "/stopnewcoin":
        active_modes["new"] = False
        send(chat_id, "❌ New dayandı")

    elif text == "/forex":
        forex(chat_id)

    elif text == "/birja":
        birja(chat_id)

    elif text == "/allstop":
        active_modes["meme"] = False
        active_modes["new"] = False
        send(chat_id, "🛑 STOP")


if __name__ == "__main__":
    main()
