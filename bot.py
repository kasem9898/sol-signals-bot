import os
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

OKX_URL = "https://www.okx.com/api/v5/market/candles"
TELEGRAM_URL = (
    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
)

INSTRUMENT = "SOL-USDT-SWAP"
TIMEFRAME = "1m"

# إعدادات استراتيجية الزخم التجريبية
MOMENTUM_1M_MIN = 0.0008       # تغير 0.08% خلال دقيقة
MOMENTUM_3M_MIN = 0.0018       # تغير 0.18% خلال 3 دقائق
VOLUME_MULTIPLIER = 1.20       # حجم أكبر من المتوسط بنسبة 20%
MIN_BODY_RATIO = 0.45          # الحد الأدنى لقوة جسم الشمعة

ATR_PERIOD = 14
STOP_ATR_MULTIPLIER = 1.5
TARGET1_R_MULTIPLIER = 1.0
TARGET2_R_MULTIPLIER = 1.5


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


def ema_series(values, period):
    if not values:
        return []

    alpha = 2 / (period + 1)
    current = values[0]
    result = [current]

    for value in values[1:]:
        current = alpha * value + (1 - alpha) * current
        result.append(current)

    return result


def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0

    changes = [
        closes[i] - closes[i - 1]
        for i in range(len(closes) - period, len(closes))
    ]

    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    if avg_gain == 0:
        return 0.0

    relative_strength = avg_gain / avg_loss

    return 100 - (100 / (1 + relative_strength))


def calculate_atr(candles, period=ATR_PERIOD):
    if len(candles) < period + 1:
        raise ValueError("Not enough candles to calculate ATR")

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

    return sum(true_ranges) / len(true_ranges)


def get_closed_candles():
    response = requests.get(
        OKX_URL,
        params={
            "instId": INSTRUMENT,
            "bar": TIMEFRAME,
            "limit": "100"
        },
        timeout=15
    )

    response.raise_for_status()
    result = response.json()

    if result.get("code") != "0":
        raise Exception(f"OKX API error: {result}")

    raw_candles = result.get("data", [])

    # OKX: confirm=1 يعني أن الشمعة مكتملة
    candles = [
        candle for candle in raw_candles
        if len(candle) > 8 and candle[8] == "1"
    ]

    # بيانات OKX تصل عادة من الأحدث إلى الأقدم
    candles.reverse()

    if len(candles) < 60:
        raise Exception("Not enough closed candles")

    return candles


def analyze():
    candles = get_closed_candles()

    closes = [float(candle[4]) for candle in candles]
    highs = [float(candle[2]) for candle in candles]
    lows = [float(candle[3]) for candle in candles]
    volumes = [float(candle[5]) for candle in candles]

    price = closes[-1]
    previous_price = closes[-2]
    price_3m_ago = closes[-4]

    last_candle = candles[-1]
    candle_time = last_candle[0]

    candle_open = float(last_candle[1])
    candle_high = float(last_candle[2])
    candle_low = float(last_candle[3])
    candle_close = float(last_candle[4])

    candle_range = candle_high - candle_low

    if candle_range <= 0:
        print("No signal: latest candle has no price range.")
        return

    # حركة السعر خلال دقيقة و3 دقائق
    momentum_1m = (price / previous_price) - 1
    momentum_3m = (price / price_3m_ago) - 1

    # متوسط حجم التداول للشموع العشرين السابقة
    previous_volumes = volumes[-21:-1]
    average_volume = sum(previous_volumes) / len(previous_volumes)

    if average_volume <= 0:
        print("No signal: invalid average volume.")
        return

    current_volume = volumes[-1]
    volume_ratio = current_volume / average_volume

    # موقع الإغلاق داخل مدى الشمعة
    close_position = (
        (candle_close - candle_low) / candle_range
    )

    body_ratio = (
        abs(candle_close - candle_open) / candle_range
    )

    # المتوسطات الأسية
    ema9 = ema_series(closes, 9)[-1]
    ema21 = ema_series(closes, 21)[-1]
    ema50 = ema_series(closes, 50)[-1]

    rsi = calculate_rsi(closes)
    atr = calculate_atr(candles)

    print(f"Closed candle: {candle_time}")
    print(f"Price: {price:.4f}")
    print(f"Momentum 1m: {momentum_1m * 100:.3f}%")
    print(f"Momentum 3m: {momentum_3m * 100:.3f}%")
    print(f"Volume ratio: {volume_ratio:.2f}x")
    print(f"Body ratio: {body_ratio:.2f}")
    print(f"Close position: {close_position:.2f}")
    print(f"EMA 9: {ema9:.4f}")
    print(f"EMA 21: {ema21:.4f}")
    print(f"EMA 50: {ema50:.4f}")
    print(f"RSI: {rsi:.2f}")
    print(f"ATR: {atr:.4f}")

    # شروط الزخم الصاعد
    long_signal = (
        momentum_1m >= MOMENTUM_1M_MIN
        and momentum_3m >= MOMENTUM_3M_MIN
        and volume_ratio >= VOLUME_MULTIPLIER
        and body_ratio >= MIN_BODY_RATIO
        and close_position >= 0.70
        and ema9 > ema21
        and price > ema50
    )

    # شروط الزخم الهابط
    short_signal = (
        momentum_1m <= -MOMENTUM_1M_MIN
        and momentum_3m <= -MOMENTUM_3M_MIN
        and volume_ratio >= VOLUME_MULTIPLIER
        and body_ratio >= MIN_BODY_RATIO
        and close_position <= 0.30
        and ema9 < ema21
        and price < ema50
    )

    if long_signal:
        signal = "LONG"

    elif short_signal:
        signal = "SHORT"

    else:
        print("No momentum signal on the latest closed candle.")
        return

    entry = price
    risk = atr * STOP_ATR_MULTIPLIER

    if risk <= 0:
        raise Exception("Invalid ATR risk calculation")

    if signal == "LONG":
        stop_loss = entry - risk

        target1 = (
            entry + risk * TARGET1_R_MULTIPLIER
        )

        target2 = (
            entry + risk * TARGET2_R_MULTIPLIER
        )

        direction = "🟢 شراء تجريبي"

    else:
        stop_loss = entry + risk

        target1 = (
            entry - risk * TARGET1_R_MULTIPLIER
        )

        target2 = (
            entry - risk * TARGET2_R_MULTIPLIER
        )

        direction = "🔴 بيع تجريبي"

    message = (
        "⚡ إشارة زخم لحظي — SOL\n"
        f"⏱ الفريم: {TIMEFRAME}\n"
        f"🕒 وقت الشمعة: {candle_time}\n"
        f"📍 الاتجاه: {direction}\n\n"
        f"💵 سعر الدخول المرجعي: {entry:.4f}\n"
        f"🛑 وقف الخسارة النظري: {stop_loss:.4f}\n"
        f"🎯 الهدف الأول: {target1:.4f}\n"
        f"🎯 الهدف الثاني: {target2:.4f}\n\n"
        f"🚀 تغير السعر خلال دقيقة: {momentum_1m * 100:.3f}%\n"
        f"📈 تغير السعر خلال 3 دقائق: {momentum_3m * 100:.3f}%\n"
        f"📊 حجم التداول مقارنة بالمتوسط: {volume_ratio:.2f}x\n"
        f"🕯️ قوة جسم الشمعة: {body_ratio:.2f}\n"
        f"RSI: {rsi:.2f}\n"
        f"ATR: {atr:.4f}\n\n"
        "🧪 هذه إشارة تجريبية وليست أمر تداول.\n"
        "⚠️ لم تُحتسب رسوم التداول والانزلاق السعري.\n"
        "⚠️ سعر الدخول مرجعي وليس سعر تنفيذ مضمونًا.\n"
        "لا تستخدمها لتنفيذ صفقة حقيقية قبل الاختبار."
    )

    send_message(message)

    print(f"Momentum signal sent: {signal}")


if __name__ == "__main__":
    try:
        analyze()
    except Exception as error:
        print("Analysis error:", error)
        raise
