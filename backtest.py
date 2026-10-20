import time
import requests
from datetime import datetime, timedelta, timezone

URL = "https://www.okx.com/api/v5/market/history-candles"
SYMBOL = "SOL-USDT-SWAP"
DAYS = 90

FEE = 0.0005
SLIPPAGE = 0.0002
RISK_PERCENT = 0.01
ATR_STOP = 1.5
REWARD_R = 2.0
START_BALANCE = 1000.0


def get_candles():
    start = int(
        (datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp()
        * 1000
    )

    data = {}
    after = None

    for _ in range(320):
        params = {
            "instId": SYMBOL,
            "bar": "5m",
            "limit": "100"
        }

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
            timestamp = int(row[0])

            # Closed candles only
            if timestamp >= start and row[8] == "1":
                data[timestamp] = row

        oldest = min(int(row[0]) for row in rows)

        print(
            "Downloaded candles:",
            len(data),
            "| Oldest:",
            datetime.fromtimestamp(
                oldest / 1000, timezone.utc
            ).strftime("%Y-%m-%d")
        )

        if oldest <= start:
            break

        if after == str(oldest):
            break

        after = str(oldest)
        time.sleep(0.15)

    candles = [data[k] for k in sorted(data)]

    if len(candles) < 3000:
        raise RuntimeError(
            f"Insufficient historical data: {len(candles)} candles. "
            "The 90-day test cannot be trusted."
        )

    return candles


def ema(values, period):
    alpha = 2 / (period + 1)
    result = [values[0]]

    for value in values[1:]:
        result.append(
            alpha * value + (1 - alpha) * result[-1]
        )

    return result


def rsi(values, period=14):
    result = [None] * len(values)

    for i in range(period, len(values)):
        changes = [
            values[j] - values[j - 1]
            for j in range(i - period + 1, i + 1)
        ]

        gains = sum(max(x, 0) for x in changes) / period
        losses = sum(max(-x, 0) for x in changes) / period

        if losses == 0:
            result[i] = 100 if gains else 50
        else:
            result[i] = 100 - 100 / (1 + gains / losses)

    return result


def atr(highs, lows, closes, period=14):
    result = [None] * len(closes)
    ranges = [None]

    for i in range(1, len(closes)):
        ranges.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))

    for i in range(period, len(closes)):
        result[i] = sum(
            ranges[i - period + 1:i + 1]
        ) / period

    return result


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
        "Current EMA9/21 + EMA50 + RSI",
        "Trend EMA50/200 + RSI",
        "20-candle breakout",
        "Trend pullback EMA20/50"
    ]

    signals = {name: [None] * len(candles) for name in names}

    for i in range(201, len(candles) - 1):
        if ats[i] is None or ats[i] <= 0 or rs[i] is None:
            continue

        # 1. Current strategy: EMA crossover
        if (
            e9[i - 1] <= e21[i - 1]
            and e9[i] > e21[i]
            and closes[i] > e50[i]
            and 50 < rs[i] < 68
        ):
            signals[names[0]][i] = "LONG"

        elif (
            e9[i - 1] >= e21[i - 1]
            and e9[i] < e21[i]
            and closes[i] < e50[i]
            and 32 < rs[i] < 50
        ):
            signals[names[0]][i] = "SHORT"

        # 2. Trend strategy
        if (
            e50[i] > e200[i]
            and e20[i - 1] <= e50[i - 1]
            and e20[i] > e50[i]
            and 52 < rs[i] < 70
        ):
            signals[names[1]][i] = "LONG"

        elif (
            e50[i] < e200[i]
            and e20[i - 1] >= e50[i - 1]
            and e20[i] < e50[i]
            and 30 < rs[i] < 48
        ):
            signals[names[1]][i] = "SHORT"

        # 3. Breakout strategy: compare with previous 20 candles
        previous_high = max(highs[i - 20:i])
        previous_low = min(lows[i - 20:i])

        if (
            closes[i] > previous_high
            and closes[i] > e50[i]
            and rs[i] > 52
        ):
            signals[names[2]][i] = "LONG"

        elif (
            closes[i] < previous_low
            and closes[i] < e50[i]
            and rs[i] < 48
        ):
            signals[names[2]][i] = "SHORT"

        # 4. Trend pullback strategy
        if (
            e20[i] > e50[i]
            and closes[i - 1] <= e20[i - 1]
            and closes[i] > e20[i]
            and 50 < rs[i] < 65
        ):
            signals[names[3]][i] = "LONG"

        elif (
            e20[i] < e50[i]
            and closes[i - 1] >= e20[i - 1]
            and closes[i] < e20[i]
            and 35 < rs[i] < 50
        ):
            signals[names[3]][i] = "SHORT"

    return opens, highs, lows, closes, ats, signals


def test_strategy(name, signals, opens, highs, lows, closes,
                  ats, start, end):
    balance = START_BALANCE
    peak = balance
    max_dd = 0.0
    trades = []
    i = start

    while i < min(end, len(closes) - 1):
        side = signals[i]

        if side not in ("LONG", "SHORT") or not ats[i]:
            i += 1
            continue

        entry_i = i + 1
        entry = opens[entry_i]

        if side == "LONG":
            entry *= 1 + SLIPPAGE
        else:
            entry *= 1 - SLIPPAGE

        distance = ats[i] * ATR_STOP
        stop = entry - distance if side == "LONG" else entry + distance
        target = (
            entry + distance * REWARD_R
            if side == "LONG"
            else entry - distance * REWARD_R
        )

        units = balance * RISK_PERCENT / distance
        exit_price = closes[entry_i]
        exit_i = entry_i

        # Maximum holding period: 4 hours
        last = min(end - 1, entry_i + 48)

        for j in range(entry_i, last + 1):
            if side == "LONG":
                stop_hit = lows[j] <= stop
                target_hit = highs[j] >= target
            else:
                stop_hit = highs[j] >= stop
                target_hit = lows[j] <= target

            # Conservative: count stop first if both hit in one candle
            if stop_hit:
                exit_price = stop
                exit_i = j
                break

            if target_hit:
                exit_price = target
                exit_i = j
                break

            exit_price = closes[j]
            exit_i = j

        if side == "LONG":
            exit_price *= 1 - SLIPPAGE
            gross = (exit_price - entry) * units
        else:
            exit_price *= 1 + SLIPPAGE
            gross = (entry - exit_price) * units

        fees = (abs(entry * units) + abs(exit_price * units)) * FEE
        net = gross - fees

        balance += net
        peak = max(peak, balance)

        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak * 100)

        trades.append(net)

        # Do not overlap simulated trades
        i = exit_i + 1

    wins = sum(1 for p in trades if p > 0)
    losses = len(trades) - wins
    profit = sum(p for p in trades if p > 0)
    loss = -sum(p for p in trades if p < 0)

    pf = profit / loss if loss > 0 else 0

    return {
        "name": name,
        "count": len(trades),
        "wins": wins,
        "losses": losses,
        "winrate": wins / len(trades) * 100 if trades else 0,
        "net": balance - START_BALANCE,
        "return_pct": (balance / START_BALANCE - 1) * 100,
        "pf": pf,
        "drawdown": max_dd
    }


def main():
    print("Starting 90-day SOLUSDT 5-minute backtest...")

    candles = get_candles()

    print("Candles downloaded:", len(candles))
    print(
        "From:",
        datetime.fromtimestamp(
            int(candles[0][0]) / 1000, timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC"),
        "to:",
        datetime.fromtimestamp(
            int(candles[-1][0]) / 1000, timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
    )

    opens, highs, lows, closes, ats, all_signals = make_signals(candles)

    split = int(len(candles) * 0.70)
    warmup = 250

    print("\nAssumptions:")
    print("Starting balance: 1000 USDT (simulation only)")
    print("Risk per trade: 1%")
    print("Stop: 1.5 ATR | Target: 2R")
    print("Estimated fee: 0.05% per side")
    print("Estimated slippage: 0.02% per side")
    print("Same-candle stop/target: stop counted first")

    results = []

    for name, signals in all_signals.items():
        development = test_strategy(
            name, signals, opens, highs, lows, closes, ats,
            warmup, split
        )

        out_sample = test_strategy(
            name, signals, opens, highs, lows, closes, ats,
            split, len(candles) - 1
        )

        results.append(out_sample)

        for label, result in [
            ("DEVELOPMENT 70%", development),
            ("UNSEEN TEST 30%", out_sample)
        ]:
            print("\n", label, "-", name)
            print("Trades:", result["count"])
            print("Wins:", result["wins"])
            print("Losses:", result["losses"])
            print("Win rate:", round(result["winrate"], 2), "%")
            print("Net P/L:", round(result["net"], 2), "USDT")
            print("Return:", round(result["return_pct"], 2), "%")
            print("Profit factor:", round(result["pf"], 2))
            print("Max drawdown:", round(result["drawdown"], 2), "%")

    print("\n=== UNSEEN PERIOD COMPARISON ===")

    for result in sorted(results, key=lambda x: x["net"], reverse=True):
        print(
            result["name"],
            "| Trades:", result["count"],
            "| Win rate:", round(result["winrate"], 1), "%",
            "| Net:", round(result["net"], 2),
            "| PF:", round(result["pf"], 2),
            "| Max DD:", round(result["drawdown"], 2), "%"
        )

    print("\nSimulation only. Past performance does not guarantee future profits.")


if __name__ == "__main__":
    main()
