import os
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

OKX_URL = "https://www.okx.com/api/v5/market/candles"
TELEGRAM_URL = (
    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
)


def send_message(message):
    response = requests.post(
        TELEGRAM_URL,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        },
        timeout=15
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise Exception(f"Telegram error: {result}")


def ema(values, period):
    alpha = 2 / (period + 1)
    result = values[0]

    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result

    return result


def calculate_rsi(closes, period=14):
    changes = [
        closes[i] - closes[i - 1]
        for i in range(len(closes) - period, len(closes))
    ]

    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    if avg_gain == 0:
        return 0.0

    return 100 - (100 / (1 + avg_gain / avg_loss))


def calculate_atr(candles, period=14):
    true_ranges = []

    for i in range(len(candles) - period, len(candles)):
        high = float(candles[i][2])
        low = float(candles[i][3])
        previous_close = float(candles[i - 1][4])

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        true_ranges.append(true_range)

    return sum(true_ranges) / period


def analyze():
    response = requests.get(
        OKX_URL,
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

    raw_candles = result.get("data", [])

    # استخدام الشموع المكتملة فقط
    candles = [
        candle for candle in raw_candles
        if candle[8] == "1"
    ]

    # ترتيب الشموع من الأقدم إلى الأحدث
    candles.reverse()

    if len(candles) < 60:
        raise Exception("Not enough closed candles")

    closes = [float(candle[4]) for candle in candles]

    price = closes[-1]
    candle_time = candles[-1][0]

    # المتوسطات الحالية
    ema9 = ema(closes[-60:], 9)
    ema21 = ema(closes[-60:], 21)
    ema50 = ema(closes[-60:], 50)

    # المتوسطات قبل إغلاق الشمعة الأخيرة
    previous_closes = closes[-61:-1]

    previous_ema9 = ema(previous_closes, 9)
    previous_ema21 = ema(previous_closes, 21)

    rsi = calculate_rsi(closes)
    atr = calculate_atr(candles)

    print(f"Closed candle: {candle_time}")
    print(f"Price: {price:.4f}")
    print(f"EMA 9: {ema9:.4f}")
    print(f"EMA 21: {ema21:.4f}")
    print(f"EMA 50: {ema50:.4f}")
    print(f"RSI: {rsi:.2f}")
    print(f"ATR: {atr:.4f}")

    signal = None

    # شراء: تقاطع صاعد مع اتجاه صاعد وزخم مناسب
    if (
        previous_ema9 <= previous_ema21
        and ema9 > ema21
        and price > ema50
        and 50 < rsi < 68
    ):
        signal = "LONG"

    # بيع: تقاطع هابط مع اتجاه هابط وزخم مناسب
    elif (
        previous_ema9 >= previous_ema21
        and ema9 < ema21
        and price < ema50
        and 32 < rsi < 50
    ):
        signal = "SHORT"

    if signal is None:
        print("No trading signal on the latest closed candle.")
        return

    entry = price
    risk = atr * 1.5

    if risk <= 0:
        raise Exception("Invalid ATR risk calculation")

    if signal == "LONG":
        stop_loss = entry - risk
        target1 = entry + risk
        target2 = entry + (2 * risk)

        direction = "LONG 🟢 شراء"

    else:
        stop_loss = entry + risk
        target1 = entry - risk
        target2 = entry - (2 * risk)

        direction = "SHORT 🔴 بيع"

    message = (
        "📊 إشارة SOLUSDT Futures\n"
        "⏱ الفريم: 5 دقائق\n"
        f"📍 الاتجاه: {direction}\n"
        f"💵 سعر الدخول المرجعي: {entry:.4f}\n"
        f"🛑 وقف الخسارة: {stop_loss:.4f}\n"
        f"🎯 الهدف الأول: {target1:.4f}\n"
        f"🎯 الهدف الثاني: {target2:.4f}\n\n"
        f"📈 EMA 9: {ema9:.4f}\n"
        f"📉 EMA 21: {ema21:.4f}\n"
        f"📊 EMA 50: {ema50:.4f}\n"
        f"RSI: {rsi:.2f}\n"
        f"ATR: {atr:.4f}\n\n"
        "⚠️ إشارة فنية تجريبية وليست ضماناً للربح.\n"
        "سعر الدخول مرجعي وقد يتغير قبل التنفيذ.\n"
        "تحقق من حجم الصفقة والرافعة والتصفية قبل الدخول."
    )

    send_message(message)

    print(f"Signal sent successfully: {signal}")


if __name__ == "__main__":
    try:
        analyze()
    except Exception as error:
        print("Analysis error:", error)
        raise
