import time
import requests
from datetime import datetime, timezone

# =========================================================
# إعدادات الاختبار
# =========================================================

BASE_URL = "https://www.okx.com"
SYMBOL = "SOL-USDT-SWAP"
TIMEFRAME = "5m"

START_BALANCE = 1000.0
RISK_PERCENT = 0.01

FEE_RATE = 0.0005
SLIPPAGE_RATE = 0.0002

LOOKBACK = 48
ATR_PERIOD = 14

STOP_BUFFER_ATR = 0.20
ZONE_ATR = 0.25
TARGET_R = 2.0
MAX_HOLD = 48

MIN_STOP_ATR = 0.30
MAX_STOP_ATR = 3.0

DAYS = 90


# =========================================================
# جلب البيانات التاريخية من OKX
# =========================================================

def get_candles():
    print("Starting support/resistance backtest...")

    all_candles = {}
    before = None
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - DAYS * 24 * 60 * 60 * 1000

    while True:
        params = {
            "instId": SYMBOL,
            "bar": TIMEFRAME,
            "limit": "100",
        }

        if before is not None:
            params["after"] = str(before)

        try:
            response = requests.get(
                BASE_URL + "/api/v5/market/history-candles",
                params=params,
                timeout=20,
            )
            response.raise_for_status()
            result = response.json()

        except Exception as exc:
            print("Download error:", exc)
            break

        if result.get("code") != "0":
            print("OKX error:", result.get("msg"))
            break

        rows = result.get("data", [])

        if not rows:
            break

        oldest_ts = None

        for row in rows:
            ts = int(row[0])
            oldest_ts = ts if oldest_ts is None else min(
                oldest_ts, ts
            )

            if start_ms <= ts <= now_ms:
                all_candles[ts] = {
                    "ts": ts,
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                    "confirm": row[8] if len(row) > 8 else "1",
                }

        if oldest_ts is None or oldest_ts <= start_ms:
            break

        before = oldest_ts

        print(
            "Candles downloaded:",
            len(all_candles),
            "| Oldest UTC:",
            datetime.fromtimestamp(
                oldest_ts / 1000,
                timezone.utc
            ).strftime("%Y-%m-%d %H:%M"),
        )

        time.sleep(0.12)

    candles = sorted(
        all_candles.values(),
        key=lambda candle: candle["ts"]
    )

    # تجاهل الشمعة الأخيرة إذا كانت ما تزال مفتوحة.
    current_ms = int(time.time() * 1000)
    interval_ms = 5 * 60 * 1000

    candles = [
        c for c in candles
        if c["confirm"] == "1"
        and c["ts"] + interval_ms <= current_ms
    ]

    print("Downloaded closed candles:", len(candles))

    if len(candles) > 0:
        print(
            "Oldest candle:",
            datetime.fromtimestamp(
                candles[0]["ts"] / 1000,
                timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC"),
        )

        print(
            "Newest candle:",
            datetime.fromtimestamp(
                candles[-1]["ts"] / 1000,
                timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC"),
        )

    return candles


# =========================================================
# حساب ATR
# =========================================================

def calculate_atr(candles, period=ATR_PERIOD):
    values = [None] * len(candles)
    true_ranges = []

    for i, candle in enumerate(candles):
        high = candle["high"]
        low = candle["low"]
        previous_close = (
            candles[i - 1]["close"] if i > 0
            else candle["close"]
        )

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

        true_ranges.append(tr)

        if i >= period - 1:
            values[i] = sum(
                true_ranges[i - period + 1:i + 1]
            ) / period

    return values


# =========================================================
# اكتشاف إشارات الدعم والمقاومة
#
# جميع المستويات محسوبة من الشموع السابقة فقط.
# لا نستخدم بيانات مستقبلية لتوليد الإشارة.
# =========================================================

def make_signals(candles):
    count = len(candles)
    atr = calculate_atr(candles)

    strategies = {
        "BOUNCE_BUY": [None] * count,
        "BOUNCE_SELL": [None] * count,
        "BREAKOUT_BUY": [None] * count,
        "BREAKOUT_SELL": [None] * count,
    }

    diagnostics = {
        name: {
            "candidates": 0,
            "valid": 0,
            "invalid_stop": 0,
        }
        for name in strategies
    }

    for i in range(max(LOOKBACK, ATR_PERIOD) + 1, count):
        current = candles[i]
        previous = candles[i - 1]

        current_atr = atr[i]

        if current_atr is None or current_atr <= 0:
            continue

        # المستويات مأخوذة من الشموع السابقة،
        # دون استخدام الشمعة الحالية.
        history = candles[i - LOOKBACK:i]

        support = min(c["low"] for c in history)
        resistance = max(c["high"] for c in history)

        zone = current_atr * ZONE_ATR
        buffer = current_atr * STOP_BUFFER_ATR

        o = current["open"]
        h = current["high"]
        l = current["low"]
        c = current["close"]

        previous_close = previous["close"]

        bullish = c > o
        bearish = c < o

        # -------------------------------------------------
        # 1. ارتداد من الدعم: شراء
        # -------------------------------------------------

        if (
            l <= support + zone
            and c > support
            and bullish
        ):
            level = support
            stop = support - buffer

            diagnostics["BOUNCE_BUY"]["candidates"] += 1

            risk_distance = c - stop

            if (
                risk_distance >= current_atr * MIN_STOP_ATR
                and risk_distance <= current_atr * MAX_STOP_ATR
            ):
                strategies["BOUNCE_BUY"][i] = {
                    "side": "long",
                    "level": level,
                    "stop": stop,
                    "atr": current_atr,
                }

                diagnostics["BOUNCE_BUY"]["valid"] += 1
            else:
                diagnostics["BOUNCE_BUY"]["invalid_stop"] += 1

        # -------------------------------------------------
        # 2. رفض المقاومة: بيع
        # -------------------------------------------------

        if (
            h >= resistance - zone
            and c < resistance
            and bearish
        ):
            level = resistance
            stop = resistance + buffer

            diagnostics["BOUNCE_SELL"]["candidates"] += 1

            risk_distance = stop - c

            if (
                risk_distance >= current_atr * MIN_STOP_ATR
                and risk_distance <= current_atr * MAX_STOP_ATR
            ):
                strategies["BOUNCE_SELL"][i] = {
                    "side": "short",
                    "level": level,
                    "stop": stop,
                    "atr": current_atr,
                }

                diagnostics["BOUNCE_SELL"]["valid"] += 1
            else:
                diagnostics["BOUNCE_SELL"]["invalid_stop"] += 1

        # -------------------------------------------------
        # 3. اختراق المقاومة: شراء
        # -------------------------------------------------

        if (
            previous_close <= resistance
            and c > resistance + buffer
            and bullish
        ):
            level = resistance
            stop = resistance - buffer

            diagnostics["BREAKOUT_BUY"]["candidates"] += 1

            risk_distance = c - stop

            if (
                risk_distance >= current_atr * MIN_STOP_ATR
                and risk_distance <= current_atr * MAX_STOP_ATR
            ):
                strategies["BREAKOUT_BUY"][i] = {
                    "side": "long",
                    "level": level,
                    "stop": stop,
                    "atr": current_atr,
                }

                diagnostics["BREAKOUT_BUY"]["valid"] += 1
            else:
                diagnostics["BREAKOUT_BUY"]["invalid_stop"] += 1

        # -------------------------------------------------
        # 4. كسر الدعم: بيع
        # -------------------------------------------------

        if (
            previous_close >= support
            and c < support - buffer
            and bearish
        ):
            level = support
            stop = support + buffer

            diagnostics["BREAKOUT_SELL"]["candidates"] += 1

            risk_distance = stop - c

            if (
                risk_distance >= current_atr * MIN_STOP_ATR
                and risk_distance <= current_atr * MAX_STOP_ATR
            ):
                strategies["BREAKOUT_SELL"][i] = {
                    "side": "short",
                    "level": level,
                    "stop": stop,
                    "atr": current_atr,
                }

                diagnostics["BREAKOUT_SELL"]["valid"] += 1
            else:
                diagnostics["BREAKOUT_SELL"]["invalid_stop"] += 1

    return strategies, diagnostics


# =========================================================
# تنفيذ الاختبار التاريخي
# =========================================================

def backtest(candles, signals, start, end):
    balance = START_BALANCE
    peak_balance = balance
    max_drawdown = 0.0

    trades = []
    i = max(start, LOOKBACK + 1)

    while i < end:
        signal = signals[i]

        if signal is None:
            i += 1
            continue

        # الدخول بسعر افتتاح الشمعة التالية،
        # وليس بسعر إغلاق شمعة الإشارة.
        entry_index = i + 1

        if entry_index >= end:
            break

        side = signal["side"]
        raw_entry = candles[entry_index]["open"]

        if side == "long":
            entry = raw_entry * (1 + SLIPPAGE_RATE)
            stop = signal["stop"]

            risk_distance = entry - stop

            if risk_distance <= 0:
                i += 1
                continue

            target = entry + risk_distance * TARGET_R

        else:
            entry = raw_entry * (1 - SLIPPAGE_RATE)
            stop = signal["stop"]

            risk_distance = stop - entry

            if risk_distance <= 0:
                i += 1
                continue

            target = entry - risk_distance * TARGET_R

        risk_cash = balance * RISK_PERCENT

        # تقدير حجم المركز. هذا نموذج تقريبي لعقد خطي،
        # وليس حسابًا دقيقًا لحجم عقود OKX أو الرافعة.
        units = risk_cash / risk_distance

        exit_price = None
        exit_index = None
        exit_reason = None

        last_index = min(entry_index + MAX_HOLD, end - 1)

        for j in range(entry_index, last_index + 1):
            candle = candles[j]

            if side == "long":
                # إذا تجاوز الافتتاح وقف الخسارة،
                # نفترض التنفيذ عند الافتتاح الأسوأ.
                if candle["open"] <= stop:
                    exit_price = candle["open"] * (
                        1 - SLIPPAGE_RATE
                    )
                    exit_reason = "STOP_GAP"
                    exit_index = j
                    break

                if candle["open"] >= target:
                    exit_price = candle["open"] * (
                        1 - SLIPPAGE_RATE
                    )
                    exit_reason = "TARGET_GAP"
                    exit_index = j
                    break

                hit_stop = candle["low"] <= stop
                hit_target = candle["high"] >= target

                # إذا لمس السعر الهدف والوقف في الشمعة
                # نفسها، نحتسب الوقف أولًا بحذر.
                if hit_stop:
                    exit_price = stop * (
                        1 - SLIPPAGE_RATE
                    )
                    exit_reason = "STOP"
                    exit_index = j
                    break

                if hit_target:
                    exit_price = target * (
                        1 - SLIPPAGE_RATE
                    )
                    exit_reason = "TARGET"
                    exit_index = j
                    break

            else:
                if candle["open"] >= stop:
                    exit_price = candle["open"] * (
                        1 + SLIPPAGE_RATE
                    )
                    exit_reason = "STOP_GAP"
                    exit_index = j
                    break

                if candle["open"] <= target:
                    exit_price = candle["open"] * (
                        1 + SLIPPAGE_RATE
                    )
                    exit_reason = "TARGET_GAP"
                    exit_index = j
                    break

                hit_stop = candle["high"] >= stop
                hit_target = candle["low"] <= target

                if hit_stop:
                    exit_price = stop * (
                        1 + SLIPPAGE_RATE
                    )
                    exit_reason = "STOP"
                    exit_index = j
                    break

                if hit_target:
                    exit_price = target * (
                        1 + SLIPPAGE_RATE
                    )
                    exit_reason = "TARGET"
                    exit_index = j
                    break

        # إذا لم يصل السعر إلى الوقف أو الهدف.
        if exit_price is None:
            exit_index = last_index
            close_price = candles[exit_index]["close"]

            if side == "long":
                exit_price = close_price * (
                    1 - SLIPPAGE_RATE
                )
            else:
                exit_price = close_price * (
                    1 + SLIPPAGE_RATE
                )

            exit_reason = "TIME_EXIT"

        if side == "long":
            gross_pnl = units * (exit_price - entry)
        else:
            gross_pnl = units * (entry - exit_price)

        entry_fee = units * entry * FEE_RATE
        exit_fee = units * exit_price * FEE_RATE

        net_pnl = gross_pnl - entry_fee - exit_fee

        balance += net_pnl

        peak_balance = max(peak_balance, balance)

        if peak_balance > 0:
            drawdown = (
                (peak_balance - balance) / peak_balance
            ) * 100
            max_drawdown = max(max_drawdown, drawdown)

        trades.append({
            "side": side,
            "entry": entry,
            "exit": exit_price,
            "pnl": net_pnl,
            "reason": exit_reason,
            "entry_index": entry_index,
            "exit_index": exit_index,
        })

        # منع الصفقات المتداخلة في الاختبار.
        i = exit_index + 1

        if balance <= 0:
            break

    return {
        "trades": trades,
        "balance": balance,
        "net": balance - START_BALANCE,
        "drawdown": max_drawdown,
    }


# =========================================================
# طباعة النتائج
# =========================================================

def print_results(name, result):
    trades = result["trades"]

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    win_rate = (
        len(wins) / len(trades) * 100
        if trades else 0.0
    )

    print(f"\n--- {name} ---")
    print("Trades:", len(trades))
    print("Wins:", len(wins))
    print("Losses:", len(losses))
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Gross profit: {gross_profit:.2f} USDT")
    print(f"Gross loss: {gross_loss:.2f} USDT")

    if profit_factor == float("inf"):
        print("Profit factor: Infinite (no losing trades)")
    else:
        print(f"Profit factor: {profit_factor:.2f}")

    print(f"Net P/L: {result['net']:.2f} USDT")
    print(
        f"Return: {result['net'] / START_BALANCE * 100:.2f}%"
    )
    print(f"Max drawdown: {result['drawdown']:.2f}%")
    print(f"Final balance: {result['balance']:.2f} USDT")

    reasons = {}

    for trade in trades:
        reasons[trade["reason"]] = (
            reasons.get(trade["reason"], 0) + 1
        )

    print("Exit reasons:", reasons)


# =========================================================
# التشغيل الرئيسي
# =========================================================

def main():
    candles = get_candles()

    if len(candles) < 1000:
        print(
            "ERROR: Not enough historical candles. "
            "Check the OKX API response."
        )
        return

    print("\nGenerating support/resistance signals...")

    strategies, diagnostics = make_signals(candles)

    split = int(len(candles) * 0.70)

    print("Total candles:", len(candles))
    print("Development candles:", split)
    print("Out-of-sample candles:", len(candles) - split)

    print("\n===== SIGNAL DIAGNOSTICS =====")

    for name, stats in diagnostics.items():
        print(f"\n{name}")
        print("Candidates:", stats["candidates"])
        print("Valid signals:", stats["valid"])
        print("Rejected stop distance:", stats["invalid_stop"])

    print("\n===== BACKTEST RESULTS =====")

    for name, signals in strategies.items():
        print("\n" + "=" * 55)
        print("STRATEGY:", name)
        print("=" * 55)

        development = backtest(
            candles,
            signals,
            start=LOOKBACK + 1,
            end=split,
        )

        out_of_sample = backtest(
            candles,
            signals,
            start=split,
            end=len(candles) - 1,
        )

        print_results("DEVELOPMENT - 70%", development)
        print_results("OUT OF SAMPLE - 30%", out_of_sample)


if __name__ == "__main__":
    main()
