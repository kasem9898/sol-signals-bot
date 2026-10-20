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
REWARD_R = 2.0
MAX_HOLD = 48

LOOKBACK = 48
ATR_PERIOD = 14
ZONE_ATR = 0.35
STOP_BUFFER_ATR = 0.25
MIN_BODY_RATIO = 0.35
MIN_RISK_ATR = 0.50
MAX_RISK_ATR = 2.50


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
            ts = int(row[0])
            if ts >= start and row[8] == "1":
                data[ts] = row

        oldest = min(int(row[0]) for row in rows)

        print(
            "Candles:", len(data),
            "| Oldest UTC:",
            datetime.fromtimestamp(
                oldest / 1000, timezone.utc
            ).strftime("%Y-%m-%d %H:%M")
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
            f"Insufficient data: {len(candles)} candles"
        )

    return candles


def ema(values, period):
    alpha = 2 / (period + 1)
    out = [values[0]]

    for value in values[1:]:
        out.append(
            alpha * value + (1 - alpha) * out[-1]
        )

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


def atr(highs, lows, closes, period=ATR_PERIOD):
    ranges = [None]
    out = [None] * len(closes)

    for i in range(1, len(closes)):
        ranges.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))

    for i in range(period, len(closes)):
        out[i] = (
            sum(ranges[i - period + 1:i + 1]) / period
        )

    return out


def make_signals(candles):
    opens = [float(c[1]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]

    e50 = ema(closes, 50)
    rs = rsi(closes)
    ats = atr(highs, lows, closes)

    signals = {
        "BOUNCE_LONG": [None] * len(candles),
        "BOUNCE_SHORT": [None] * len(candles),
        "RETEST_LONG": [None] * len(candles),
        "RETEST_SHORT": [None] * len(candles)
    }

    # Retest state: a breakout must happen first.
    breakout_up = [False] * len(candles)
    breakout_down = [False] * len(candles)

    for i in range(LOOKBACK + 2, len(candles) - 1):
        if ats[i] is None or ats[i] <= 0 or rs[i] is None:
            continue

        zone = ats[i] * ZONE_ATR
        candle_range = highs[i] - lows[i]

        if candle_range <= 0:
            continue

        body_ratio = abs(closes[i] - opens[i]) / candle_range
        bullish = closes[i] > opens[i]
        bearish = closes[i] < opens[i]

        # Levels use only candles before the current candle.
        support = min(lows[i - LOOKBACK:i])
        resistance = max(highs[i - LOOKBACK:i])

        # Previous levels for detecting a breakout.
        old_support = min(lows[i - LOOKBACK - 1:i - 1])
        old_resistance = max(highs[i - LOOKBACK - 1:i - 1])

        # Detect breakout on the previous closed candle.
        breakout_up[i - 1] = closes[i - 1] > old_resistance
        breakout_down[i - 1] = closes[i - 1] < old_support

        # 1. Support bounce: bullish rejection from support.
        support_touch = lows[i] <= support + zone
        support_reclaim = closes[i] > support + zone * 0.25

        if (
            support_touch
            and support_reclaim
            and bullish
            and body_ratio >= MIN_BODY_RATIO
            and closes[i] > e50[i]
            and 45 < rs[i] < 68
        ):
            signals["BOUNCE_LONG"][i] = "LONG"

        # 2. Resistance bounce: bearish rejection.
        resistance_touch = highs[i] >= resistance - zone
        resistance_reject = closes[i] < resistance - zone * 0.25

        if (
            resistance_touch
            and resistance_reject
            and bearish
            and body_ratio >= MIN_BODY_RATIO
            and closes[i] < e50[i]
            and 32 < rs[i] < 55
        ):
            signals["BOUNCE_SHORT"][i] = "SHORT"

        # 3. Breakout and retest.
        # Require a prior candle to close outside the previous level.
        if (
            breakout_up[i - 1]
            and lows[i] <= old_resistance + zone
            and closes[i] > old_resistance + zone * 0.25
            and bullish
            and closes[i] > e50[i]
            and rs[i] > 50
        ):
            signals["RETEST_LONG"][i] = "LONG"

        if (
            breakout_down[i - 1]
            and highs[i] >= old_support - zone
            and closes[i] < old_support - zone * 0.25
            and bearish
            and closes[i] < e50[i]
            and rs[i] < 50
        ):
            signals["RETEST_SHORT"][i] = "SHORT"

    return opens, highs, lows, closes, ats, signals


def test_strategy(name, signals, opens, highs, lows, closes,
                  ats, start, end, side_filter=None):
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

        if side_filter and side != side_filter:
            i += 1
            continue

        entry_i = i + 1
        raw_entry = opens[entry_i]

        entry = raw_entry * (
            1 + SLIPPAGE if side == "LONG"
            else 1 - SLIPPAGE
        )

        # Define stop behind the relevant zone.
        left = max(0, i - LOOKBACK)
        if side == "LONG":
            zone_level = min(lows[left:i])
            stop = zone_level - ats[i] * STOP_BUFFER_ATR
            distance = entry - stop
        else:
            zone_level = max(highs[left:i])
            stop = zone_level + ats[i] * STOP_BUFFER_ATR
            distance = stop - entry

        if (
            distance <= 0
            or distance < ats[i] * MIN_RISK_ATR
            or distance > ats[i] * MAX_RISK_ATR
        ):
            i += 1
            continue

        target = (
            entry + distance * REWARD_R
            if side == "LONG"
            else entry - distance * REWARD_R
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
                max_dd,
                (peak - balance) / peak * 100
            )

        trades.append({
            "net": net,
            "side": side,
            "entry_i": entry_i,
            "exit_i": exit_i
        })

        i = exit_i + 1

    profits = [t["net"] for t in trades]
    wins = sum(1 for p in profits if p > 0)
    losses = sum(1 for p in profits if p < 0)
    gross_profit = sum(p for p in profits if p > 0)
    gross_loss = -sum(p for p in profits if p < 0)
    pf = gross_profit / gross_loss if gross_loss > 0 else 0.0

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


def print_result(result, label):
    print(f"--- {label} ---")
    print(f"Trades: {result['count']}")
    print(f"Wins: {result['wins']}")
    print(f"Losses: {result['losses']}")
    print(f"Win rate: {result['winrate']:.2f}%")
    print(f"Net P/L: {result['net']:.2f} USDT")
    print(f"Return: {result['return_pct']:.2f}%")
    print(f"Profit factor: {result['pf']:.2f}")
    print(f"Max drawdown: {result['drawdown']:.2f}%")
    print(f"Final balance: {result['final_balance']:.2f} USDT")


def main():
    print("Starting SOL support/resistance backtest...")
    candles = get_candles()
    print(f"Downloaded candles: {len(candles)}")

    opens, highs, lows, closes, ats, signals = make_signals(candles)

    total = len(candles)
    split = int(total * 0.70)

    print(f"Development candles: {split}")
    print(f"Out-of-sample candles: {total - split}")

    for name, strategy_signals in signals.items():
        print(f"\n===== Strategy: {name} =====")

        for side_filter, side_name in [
            ("LONG", "BUY ONLY"),
            ("SHORT", "SELL ONLY"),
            (None, "BOTH SIDES")
        ]:
            print(f"\nDirection: {side_name}")

            development = test_strategy(
                name, strategy_signals, opens, highs, lows,
                closes, ats, LOOKBACK + 2, split, side_filter
            )

            out_sample = test_strategy(
                name, strategy_signals, opens, highs, lows,
                closes, ats, split, total - 1, side_filter
            )

            print_result(development, "Development (70%)")
            print_result(out_sample, "Out-of-sample (30%)")

    print("\n===== BACKTEST FINISHED =====")


if __name__ == "__main__":
    main()
