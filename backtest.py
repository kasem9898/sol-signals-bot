import time
import requests
from datetime import datetime, timedelta, timezone

URL = "https://www.okx.com/api/v5/market/history-candles"
SYMBOL = "SOL-USDT-SWAP"
DAYS = 90

START_BALANCE = 1000.0
RISK_PERCENT = 0.01
FEE = 0.0005
SLIPPAGE = 0.0002
ATR_STOP = 1.5
REWARD_R = 2.0
MAX_HOLD = 48


def get_candles():
    start = int(
        (datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp()
        * 1000
    )
    data = {}
    after = None

    for _ in range(320):
        params = {"instId": SYMBOL, "bar": "5m", "limit": "100"}
        if after:
            params["after"] = after

        response = requests.get(URL, params=params, timeout=20)
        response.raise_for_status()
        result = response.json()

        if result.get("code") != "0":
            raise RuntimeError(result)

        rows = result.get("data", [])
        if not rows:
            break

        for row in rows:
            ts = int(row[0])
            if ts >= start and row[8] == "1":
                data[ts] = row

        oldest = min(int(row[0]) for row in rows)
        print("Candles:", len(data), "| Oldest UTC:",
              datetime.fromtimestamp(
                  oldest / 1000, timezone.utc
              ).strftime("%Y-%m-%d %H:%M"))

        if oldest <= start:
            break
        if after == str(oldest):
            break

        after = str(oldest)
        time.sleep(0.15)

    candles = [data[k] for k in sorted(data)]
    if len(candles) < 3000:
        raise RuntimeError(
            f"Insufficient data: {len(candles)} candles"
        )

    return candles


def ema(values, period):
    alpha = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def rsi(values, period=14):
    out = [None] * len(values)
    for i in range(period, len(values)):
        changes = [
            values[j] - values[j - 1]
            for j in range(i - period + 1, i + 1)
        ]
        gains = sum(max(x, 0) for x in changes) / period
        losses = sum(max(-x, 0) for x in changes) / period
        if losses == 0:
            out[i] = 100 if gains else 50
        else:
            out[i] = 100 - 100 / (1 + gains / losses)
    return out


def atr(highs, lows, closes, period=14):
    ranges = [None]
    out = [None] * len(closes)

    for i in range(1, len(closes)):
        ranges.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))

    for i in range(period, len(closes)):
        out[i] = sum(ranges[i - period + 1:i + 1]) / period

    return out


def make_signals(candles):
    opens = [float(c[1]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]

    e9 = ema(closes, 9)
    e20 = ema(closes, 20)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    rs = rsi(closes)
    ats = atr(highs, lows, closes)

    names = [
        "EMA9/21 + EMA50 + RSI",
        "EMA50/200 trend + RSI",
        "20-candle breakout",
        "EMA20/50 pullback",
        "Support/Resistance + Supply/Demand"
    ]

    signals = {name: [None] * len(candles) for name in names}

    # Parameters for the support/resistance strategy
    LOOKBACK = 48
    ZONE_ATR = 0.35
    MIN_BODY_RATIO = 0.35

    for i in range(201, len(candles) - 1):
        if ats[i] is None or ats[i] <= 0 or rs[i] is None:
            continue

        # Strategy 1: EMA9/21 + EMA50 + RSI
        if (e9[i - 1] <= e21[i - 1] and e9[i] > e21[i]
                and closes[i] > e50[i] and 50 < rs[i] < 68):
            signals[names[0]][i] = "LONG"
        elif (e9[i - 1] >= e21[i - 1] and e9[i] < e21[i]
                and closes[i] < e50[i] and 32 < rs[i] < 50):
            signals[names[0]][i] = "SHORT"

        # Strategy 2: EMA50/200 trend + RSI
        if (e50[i] > e200[i] and e20[i - 1] <= e50[i - 1]
                and e20[i] > e50[i] and 52 < rs[i] < 70):
            signals[names[1]][i] = "LONG"
        elif (e50[i] < e200[i] and e20[i - 1] >= e50[i - 1]
                and e20[i] < e50[i] and 30 < rs[i] < 48):
            signals[names[1]][i] = "SHORT"

        # Strategy 3: 20-candle breakout
        prev_high = max(highs[i - 20:i])
        prev_low = min(lows[i - 20:i])

        if closes[i] > prev_high and closes[i] > e50[i] and rs[i] > 52:
            signals[names[2]][i] = "LONG"
        elif closes[i] < prev_low and closes[i] < e50[i] and rs[i] < 48:
            signals[names[2]][i] = "SHORT"

        # Strategy 4: EMA20/50 pullback
        if (e20[i] > e50[i] and closes[i - 1] <= e20[i - 1]
                and closes[i] > e20[i] and 50 < rs[i] < 65):
            signals[names[3]][i] = "LONG"
        elif (e20[i] < e50[i] and closes[i - 1] >= e20[i - 1]
                and closes[i] < e20[i] and 35 < rs[i] < 50):
            signals[names[3]][i] = "SHORT"

        # Strategy 5: Support/Resistance + Supply/Demand
        # Levels are calculated only from candles before the current candle.
        if i < LOOKBACK + 1:
            continue

        resistance = max(highs[i - LOOKBACK:i])
        support = min(lows[i - LOOKBACK:i])

        zone = ats[i] * ZONE_ATR
        candle_range = highs[i] - lows[i]

        if candle_range <= 0:
            continue

        body_ratio = abs(closes[i] - opens[i]) / candle_range

        bullish = closes[i] > opens[i]
        bearish = closes[i] < opens[i]

        # Support rejection / demand reaction
        support_touch = lows[i] <= support + zone
        support_reclaim = closes[i] > support + zone * 0.25

        bullish_engulfing = (
            closes[i] > opens[i]
            and closes[i - 1] < opens[i - 1]
            and opens[i] <= closes[i - 1]
            and closes[i] >= opens[i - 1]
        )

        if (
            support_touch
            and support_reclaim
            and bullish
            and (body_ratio >= MIN_BODY_RATIO or bullish_engulfing)
            and closes[i] > e50[i]
            and 45 < rs[i] < 68
        ):
            signals[names[4]][i] = "LONG"

        # Resistance rejection / supply reaction
        resistance_touch = highs[i] >= resistance - zone
        resistance_reject = closes[i] < resistance - zone * 0.25

        bearish_engulfing = (
            closes[i] < opens[i]
            and closes[i - 1] > opens[i - 1]
            and opens[i] >= closes[i - 1]
            and closes[i] <= opens[i - 1]
        )

        if (
            resistance_touch
            and resistance_reject
            and bearish
            and (body_ratio >= MIN_BODY_RATIO or bearish_engulfing)
            and closes[i] < e50[i]
            and 32 < rs[i] < 55
        ):
            signals[names[4]][i] = "SHORT"

        # Breakout and retest
        # Check the breakout using only the preceding candle.
        old_resistance = max(highs[i - LOOKBACK - 1:i - 1])
        old_support = min(lows[i - LOOKBACK - 1:i - 1])

        # Bullish retest: prior candle closed above resistance;
        # current candle retests and closes back above it.
        if (
            closes[i - 1] > old_resistance
            and lows[i] <= old_resistance + zone
            and closes[i] > old_resistance + zone * 0.25
            and bullish
            and closes[i] > e50[i]
            and rs[i] > 50
        ):
            signals[names[4]][i] = "LONG"

        # Bearish retest: prior candle closed below support;
        # current candle retests and closes back below it.
        elif (
            closes[i - 1] < old_support
            and highs[i] >= old_support - zone
            and closes[i] < old_support - zone * 0.25
            and bearish
            and closes[i] < e50[i]
            and rs[i] < 50
        ):
            signals[names[4]][i] = "SHORT"

    return opens, highs, lows, closes, ats, signals

def test_strategy(name, signals, opens, highs, lows, closes,
                  ats, start, end):
    balance = START_BALANCE
    peak = balance
    max_dd = 0.0
    trades = []
    i = start
    end = min(end, len(closes) - 1)

    while i < end and balance > 0:
        side = signals[i]
        if side not in ("LONG", "SHORT") or not ats[i]:
            i += 1
            continue

        entry_i = i + 1
        raw_entry = opens[entry_i]
        entry = raw_entry * (
            1 + SLIPPAGE if side == "LONG" else 1 - SLIPPAGE
        )

        distance = ats[i] * ATR_STOP
        if distance <= 0 or entry <= 0:
            i += 1
            continue

        stop = entry - distance if side == "LONG" else entry + distance
        target = (
            entry + distance * REWARD_R
            if side == "LONG" else entry - distance * REWARD_R
        )

        risk_cash = balance * RISK_PERCENT
        units = risk_cash / distance
        if units <= 0:
            break

        exit_price = closes[entry_i]
        exit_i = entry_i
        last = min(end - 1, entry_i + MAX_HOLD)

        for j in range(entry_i, last + 1):
            if side == "LONG":
                if opens[j] <= stop:
                    exit_price, exit_i = opens[j], j
                    break
                if lows[j] <= stop:
                    exit_price, exit_i = stop, j
                    break
                if highs[j] >= target:
                    exit_price, exit_i = target, j
                    break
            else:
                if opens[j] >= stop:
                    exit_price, exit_i = opens[j], j
                    break
                if highs[j] >= stop:
                    exit_price, exit_i = stop, j
                    break
                if lows[j] <= target:
                    exit_price, exit_i = target, j
                    break

            exit_price, exit_i = closes[j], j

        if side == "LONG":
            exit_price *= 1 - SLIPPAGE
            gross = (exit_price - entry) * units
        else:
            exit_price *= 1 + SLIPPAGE
            gross = (entry - exit_price) * units

        fees = (
            abs(entry * units) + abs(exit_price * units)
        ) * FEE
        net = gross - fees

        balance = max(0.0, balance + net)
        peak = max(peak, balance)

        if peak > 0:
            max_dd = max(
                max_dd, (peak - balance) / peak * 100
            )

        trades.append(net)
        i = exit_i + 1

    wins = sum(1 for p in trades if p > 0)
    losses = sum(1 for p in trades if p < 0)
    gross_profit = sum(p for p in trades if p > 0)
    gross_loss = -sum(p for p in trades if p < 0)
    pf = gross_profit / gross_loss if gross_loss else 0.0

    return {
        "name": name,
        "count": len(trades),
        "wins": wins,
        "losses": losses,
        "winrate": wins / len(trades) * 100 if trades else 0,
        "net": balance - START_BALANCE,
        "return_pct": (balance / START_BALANCE - 1) * 100,
        "pf": pf,
        "drawdown": max_dd,
        "final_balance": balance
    }

def main():
    print("Starting SOL-USDT-SWAP backtest...")

    candles = get_candles()

    if not candles:
        print("ERROR: No candles were downloaded.")
        return

    print(f"Downloaded candles: {len(candles)}")

    opens, highs, lows, closes, ats, signals = make_signals(candles)

    total = len(candles)
    split = int(total * 0.70)

    print(f"Development candles: {split}")
    print(f"Out-of-sample candles: {total - split}")
    print("\n===== BACKTEST RESULTS =====")

    for name, strategy_signals in signals.items():
        development = test_strategy(
            name, strategy_signals, opens, highs, lows, closes,
            ats, 201, split
        )

        out_sample = test_strategy(
            name, strategy_signals, opens, highs, lows, closes,
            ats, split, total - 1
        )

        print(f"\nStrategy: {name}")

        print("--- Development (70%) ---")
        print(f"Trades: {development['count']}")
        print(f"Win rate: {development['winrate']:.2f}%")
        print(f"Net P/L: {development['net']:.2f} USDT")
        print(f"Return: {development['return_pct']:.2f}%")
        print(f"Profit factor: {development['pf']:.2f}")
        print(f"Max drawdown: {development['drawdown']:.2f}%")
        print(f"Final balance: {development['final_balance']:.2f} USDT")

        print("--- Out-of-sample (30%) ---")
        print(f"Trades: {out_sample['count']}")
        print(f"Win rate: {out_sample['winrate']:.2f}%")
        print(f"Net P/L: {out_sample['net']:.2f} USDT")
        print(f"Return: {out_sample['return_pct']:.2f}%")
        print(f"Profit factor: {out_sample['pf']:.2f}")
        print(f"Max drawdown: {out_sample['drawdown']:.2f}%")
        print(f"Final balance: {out_sample['final_balance']:.2f} USDT")

    print("\n===== BACKTEST FINISHED =====")


if __name__ == "__main__":
    main()
