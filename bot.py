import os
import time
import requests

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BINANCE_URL = "https://www.okx.com/api/v5/market/candles"
TELEGRAM_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

last_signal = None
last_candle = None


def send_message(message):
response = requests.post(
TELEGRAM_URL,
data={"chat_id": CHAT_ID, "text": message},
timeout=15
)
print("Telegram response:", response.status_code, response.text)
response.raise_for_status()


def ema(values, period):
    result = values[0]
    alpha = 2 / (period + 1)

    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result

    return result


def analyze():
    global last_signal, last_candle

    response = requests.get(
        BINANCE_URL,
        params={
            "instId": "SOL-USDT-SWAP",
            "bar": "5m",
            "limit": "100"
        },
        timeout=15
    )
    response.raise_for_status()
    result = response.json()

    if result.get("code") != "0":
        raise Exception(f"OKX API error: {result}")

    candles = result["data"]

    # نستخدم الشموع المكتملة فقط
    closed = [candle for candle in candles if candle[8] == "1"]

    if len(closed) < 30:
        raise Exception("Not enough closed candles")

    # ترتيب الشموع من الأقدم إلى الأحدث
    closed = list(reversed(closed))

    closes = [float(candle[4]) for candle in closed]
    candle_time = closed[-1][0]
    price = closes[-1]

    if candle_time == last_candle:
        return

    last_candle = candle_time

    ema9 = ema(closes[-60:], 9)
    ema21 = ema(closes[-60:], 21)

    changes = [
        closes[i] - closes[i - 1]
        for i in range(len(closes) - 14, len(closes))
    ]

    gains = [max(change, 0) for change in changes[-14:]]
    losses = [max(-change, 0) for change in changes[-14:]]

    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14

    if avg_loss == 0:
        rsi = 100
    else:
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))

    signal = None

    if ema9 > ema21 and 50 < rsi < 70:
        signal = "LONG 🟢 شراء"
    elif ema9 < ema21 and 30 < rsi < 50:
        signal = "SHORT 🔴 بيع"

    if signal and signal != last_signal:
        message = (
            f"📊 إشارة SOLUSDT Futures\n"
            f"⏱ الفريم: 5 دقائق\n"
            f"📍 الإشارة: {signal}\n"
            f"💵 السعر: {price:.4f}\n"
            f"RSI: {rsi:.2f}\n"
            f"EMA 9: {ema9:.4f}\n"
            f"EMA 21: {ema21:.4f}\n\n"
            f"⚠️ إشارة فنية وليست ضماناً للربح.\n"
            f"حدد وقف الخسارة قبل أي صفقة."
        )

        send_message(message)
        last_signal = signal
        
        
try:
    analyze()
except Exception as error:
    print("Analysis error:", error)
    raise
