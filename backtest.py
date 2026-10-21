import time
import requests
from datetime import datetime, timezone

# =========================================================
# 1-minute momentum backtest for SOL-USDT-SWAP on OKX
# This script only downloads data and runs a historical test.
# It does NOT place real orders and does not modify bot.py.
# =========================================================

BASE_URL = "https://www.okx.com"
SYMBOL = "SOL-USDT-SWAP"
TIMEFRAME = "1m"
INTERVAL_MS = 60_000

START_BALANCE = 1000.0
RISK_PERCENT = 0.01

# Illustrative assumptions; replace with your actual account fee tier.
FEE_RATE = 0.0005          # per side, as a fraction of traded notional
SLIPPAGE_RATE = 0.0002     # per side, as a fraction of price
MAX_NOTIONAL_MULTIPLE = 1.0  # cap position notional at 1x current balance

ATR_PERIOD = 14
VOLUME_PERIOD = 20
EMA_FAST = 9
EMA_SLOW = 21
MAX_HOLD = 10              # exit after at most 10 one-minute candles
STOP_ATR = 1.2
TARGET_R = 1.5
DAYS = 90
DEV_FRACTION = 0.60
VALIDATION_FRACTION = 0.20  # final 20% is a chronological holdout

# Compare a small, predefined set of momentum thresholds.
# Do not pick a configuration using the out-of-sample data and then
# treat that same data as a fresh, unbiased test.
CONFIGS = [
    {"name": "Mild",   "mom1": 0.0005, "mom3": 0.0012, "vol_mult": 1.2, "body_min": 0.45},
    {"name": "Base",   "mom1": 0.0008, "mom3": 0.0018, "vol_mult": 1.2, "body_min": 0.45},
    {"name": "Strict-no-filter", "mom1": 0.0010, "mom3": 0.0025, "vol_mult": 1.3, "body_min": 0.50, "adx_min": 0, "sep_atr_min": 0.0},
    {"name": "Strict-ADX18", "mom1": 0.0010, "mom3": 0.0025, "vol_mult": 1.3, "body_min": 0.50, "adx_min": 18, "sep_atr_min": 0.0},
    {"name": "Strict-ADX22-separation", "mom1": 0.0010, "mom3": 0.0025, "vol_mult": 1.3, "body_min": 0.50, "adx_min": 22, "sep_atr_min": 0.15},
]


def utc_string(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def get_candles():
    print("Downloading OKX 1-minute historical candles...")
    all_candles = {}
    before = None
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - DAYS * 24 * 60 * 60 * 1000
    session = requests.Session()

    while True:
        params = {"instId": SYMBOL, "bar": TIMEFRAME, "limit": "300"}
        if before is not None:
            params["after"] = str(before)

        try:
            response = session.get(
                BASE_URL + "/api/v5/market/history-candles",
                params=params,
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            print("Download error:", exc)
            break

        if payload.get("code") != "0":
            print("OKX API error:", payload.get("msg", "unknown error"))
            break

        rows = payload.get("data", [])
        if not rows:
            break

        oldest_ts = None
        for row in rows:
            ts = int(row[0])
            oldest_ts = ts if oldest_ts is None else min(oldest_ts, ts)
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

        if oldest_ts is None or oldest_ts <= start_ms or oldest_ts == before:
            break

        before = oldest_ts
        if len(all_candles) % 3000 < 300:
            print("Candles collected:", len(all_candles), "| oldest:", utc_string(oldest_ts))
        time.sleep(0.15)

    candles = sorted(all_candles.values(), key=lambda x: x["ts"])
    current_ms = int(time.time() * 1000)
    candles = [
        c for c in candles
        if c["confirm"] == "1" and c["ts"] + INTERVAL_MS <= current_ms
    ]

    # Remove duplicate timestamps and any gaps are left visible for diagnostics.
    unique = []
    seen = set()
    for c in candles:
        if c["ts"] not in seen:
            unique.append(c)
            seen.add(c["ts"])
    candles = unique

    print("Closed candles:", len(candles))
    if candles:
        print("Range:", utc_string(candles[0]["ts"]), "to", utc_string(candles[-1]["ts"]))
        gaps = sum(
            1 for i in range(1, len(candles))
            if candles[i]["ts"] - candles[i - 1]["ts"] > INTERVAL_MS
        )
        print("Time gaps larger than 1 minute:", gaps)
    return candles


def calculate_indicators(candles):
    n = len(candles)
    close = [c["close"] for c in candles]
    high = [c["high"] for c in candles]
    low = [c["low"] for c in candles]
    volume = [c["volume"] for c in candles]

    atr = [None] * n
    ema_fast = [None] * n
    ema_slow = [None] * n
    mom1 = [None] * n
    mom3 = [None] * n
    prior_volume_avg = [None] * n
    body_ratio = [None] * n
    close_location = [None] * n
    adx = [None] * n

    tr_values = []
    plus_dm_values = []
    minus_dm_values = []
    dx_values = []
    alpha_fast = 2 / (EMA_FAST + 1)
    alpha_slow = 2 / (EMA_SLOW + 1)
    ef = es = None

    for i in range(n):
        prev_close = close[i - 1] if i else close[i]
        tr = max(
            high[i] - low[i],
            abs(high[i] - prev_close),
            abs(low[i] - prev_close),
        )
        tr_values.append(tr)
        up_move = high[i] - high[i - 1] if i else 0.0
        down_move = low[i - 1] - low[i] if i else 0.0
        plus_dm_values.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm_values.append(down_move if down_move > up_move and down_move > 0 else 0.0)

        if i == 0:
            ef = es = close[i]
        else:
            ef = alpha_fast * close[i] + (1 - alpha_fast) * ef
            es = alpha_slow * close[i] + (1 - alpha_slow) * es
        ema_fast[i] = ef
        ema_slow[i] = es

        if i >= ATR_PERIOD - 1:
            atr[i] = sum(tr_values[i - ATR_PERIOD + 1:i + 1]) / ATR_PERIOD

        if i >= 1 and close[i - 1] != 0:
            mom1[i] = close[i] / close[i - 1] - 1
        if i >= 3 and close[i - 3] != 0:
            mom3[i] = close[i] / close[i - 3] - 1

        # Average of previous candles only, excluding the current candle.
        if i >= VOLUME_PERIOD:
            prior_volume_avg[i] = sum(volume[i - VOLUME_PERIOD:i]) / VOLUME_PERIOD

        candle_range = high[i] - low[i]
        if candle_range > 0:
            body_ratio[i] = abs(close[i] - candles[i]["open"]) / candle_range
            close_location[i] = (close[i] - low[i]) / candle_range

        # Simple rolling-window ADX approximation (not Wilder-smoothed ADX).
        if i >= ATR_PERIOD - 1:
            tr_sum = sum(tr_values[i - ATR_PERIOD + 1:i + 1])
            p_sum = sum(plus_dm_values[i - ATR_PERIOD + 1:i + 1])
            m_sum = sum(minus_dm_values[i - ATR_PERIOD + 1:i + 1])
            if tr_sum > 0:
                pdi = 100.0 * p_sum / tr_sum
                mdi = 100.0 * m_sum / tr_sum
                denom = pdi + mdi
                dx_values.append(100.0 * abs(pdi - mdi) / denom if denom else 0.0)
            else:
                dx_values.append(0.0)
            if len(dx_values) >= ATR_PERIOD:
                adx[i] = sum(dx_values[-ATR_PERIOD:]) / ATR_PERIOD

    return {
        "atr": atr, "ema_fast": ema_fast, "ema_slow": ema_slow, "adx": adx,
        "mom1": mom1, "mom3": mom3, "prior_volume_avg": prior_volume_avg,
        "body_ratio": body_ratio, "close_location": close_location,
    }


def make_signals(candles, ind, cfg):
    signals = [None] * len(candles)
    for i in range(max(ATR_PERIOD, VOLUME_PERIOD, 3), len(candles)):
        atr = ind["atr"][i]
        avg_vol = ind["prior_volume_avg"][i]
        m1, m3 = ind["mom1"][i], ind["mom3"][i]
        body = ind["body_ratio"][i]
        loc = ind["close_location"][i]
        if (atr is None or atr <= 0 or avg_vol is None or avg_vol <= 0
                or m1 is None or m3 is None or body is None or loc is None):
            continue

        c = candles[i]
        vol_ok = c["volume"] >= avg_vol * cfg["vol_mult"]
        body_ok = body >= cfg["body_min"]
        bullish = c["close"] > c["open"]
        bearish = c["close"] < c["open"]
        adx_value = ind["adx"][i]
        if cfg.get("adx_min", 0) > 0 and (adx_value is None or adx_value < cfg["adx_min"]):
            continue
        separation_atr = abs(ind["ema_fast"][i] - ind["ema_slow"][i]) / atr
        if separation_atr < cfg.get("sep_atr_min", 0.0):
            continue

        trend_up = ind["ema_fast"][i] > ind["ema_slow"][i]
        trend_down = ind["ema_fast"][i] < ind["ema_slow"][i]

        if (m1 >= cfg["mom1"] and m3 >= cfg["mom3"] and vol_ok
                and body_ok and loc >= 0.70 and bullish and trend_up):
            signals[i] = {"side": "long", "atr": atr}
        elif (m1 <= -cfg["mom1"] and m3 <= -cfg["mom3"] and vol_ok
                and body_ok and loc <= 0.30 and bearish and trend_down):
            signals[i] = {"side": "short", "atr": atr}
    return signals


def backtest(candles, signals, start, end):
    balance = START_BALANCE
    peak = balance
    max_dd = 0.0
    trades = []
    i = max(start, ATR_PERIOD, VOLUME_PERIOD) 

    while i < end - 1:
        sig = signals[i]
        if sig is None:
            i += 1
            continue

        entry_i = i + 1
        if entry_i >= end:
            break

        side = sig["side"]
        raw_entry = candles[entry_i]["open"]
        entry = raw_entry * (1 + SLIPPAGE_RATE if side == "long" else 1 - SLIPPAGE_RATE)
        stop_distance = sig["atr"] * STOP_ATR
        if stop_distance <= 0:
            i += 1
            continue

        if side == "long":
            stop = entry - stop_distance
            target = entry + stop_distance * TARGET_R
        else:
            stop = entry + stop_distance
            target = entry - stop_distance * TARGET_R

        entry_balance = balance
        risk_cash = max(0.0, entry_balance) * RISK_PERCENT
        units_by_risk = risk_cash / stop_distance
        units_by_notional = max(0.0, balance) * MAX_NOTIONAL_MULTIPLE / entry
        units = min(units_by_risk, units_by_notional)
        if units <= 0:
            break

        exit_price = None
        exit_reason = None
        exit_i = None
        last_i = min(entry_i + MAX_HOLD - 1, end - 1)

        for j in range(entry_i, last_i + 1):
            candle = candles[j]
            if side == "long":
                # Conservative gap handling; stop wins if both levels are touched.
                if candle["open"] <= stop:
                    exit_price = candle["open"] * (1 - SLIPPAGE_RATE)
                    exit_reason, exit_i = "STOP_GAP", j
                    break
                if candle["open"] >= target:
                    exit_price = candle["open"] * (1 - SLIPPAGE_RATE)
                    exit_reason, exit_i = "TARGET_GAP", j
                    break
                if candle["low"] <= stop:
                    exit_price = stop * (1 - SLIPPAGE_RATE)
                    exit_reason, exit_i = "STOP", j
                    break
                if candle["high"] >= target:
                    exit_price = target * (1 - SLIPPAGE_RATE)
                    exit_reason, exit_i = "TARGET", j
                    break
            else:
                if candle["open"] >= stop:
                    exit_price = candle["open"] * (1 + SLIPPAGE_RATE)
                    exit_reason, exit_i = "STOP_GAP", j
                    break
                if candle["open"] <= target:
                    exit_price = candle["open"] * (1 + SLIPPAGE_RATE)
                    exit_reason, exit_i = "TARGET_GAP", j
                    break
                if candle["high"] >= stop:
                    exit_price = stop * (1 + SLIPPAGE_RATE)
                    exit_reason, exit_i = "STOP", j
                    break
                if candle["low"] <= target:
                    exit_price = target * (1 + SLIPPAGE_RATE)
                    exit_reason, exit_i = "TARGET", j
                    break

        if exit_price is None:
            exit_i = last_i
            close_price = candles[exit_i]["close"]
            exit_price = close_price * (1 - SLIPPAGE_RATE if side == "long" else 1 + SLIPPAGE_RATE)
            exit_reason = "TIME_EXIT"

        gross_pnl = units * ((exit_price - entry) if side == "long" else (entry - exit_price))
        fees = units * entry * FEE_RATE + units * exit_price * FEE_RATE
        net_pnl = gross_pnl - fees
        balance += net_pnl
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak * 100)

        trades.append({
            "side": side, "pnl": net_pnl, "reason": exit_reason,
            "entry_ts": candles[entry_i]["ts"], "exit_ts": candles[exit_i]["ts"],
            "entry": entry, "exit": exit_price,
            "planned_risk_pct": risk_cash / entry_balance * 100 if entry_balance > 0 else 0.0,
            "actual_stop_risk_pct": (units * stop_distance / entry_balance * 100) if entry_balance > 0 else 0.0,
        })
        i = exit_i + 1  # no overlapping positions

        if balance <= 0:
            break

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0)
    reasons = {}
    sides = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        sides.setdefault(t["side"], []).append(t["pnl"])

    return {
        "trades": trades, "balance": balance, "net": balance - START_BALANCE,
        "drawdown": max_dd, "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0.0,
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "profit_factor": pf, "reasons": reasons, "sides": sides,
        "avg_actual_stop_risk_pct": sum(t["actual_stop_risk_pct"] for t in trades) / len(trades) if trades else 0.0,
    }


def print_results(label, result):
    pf = result["profit_factor"]
    pf_text = "inf" if pf == float("inf") else f"{pf:.2f}"
    print(f"\n{label}")
    print("-" * 48)
    print("Trades:", len(result["trades"]))
    print("Wins / losses:", result["wins"], "/", result["losses"])
    print(f"Win rate: {result['win_rate']:.2f}%")
    print(f"Gross profit: {result['gross_profit']:.2f} USDT")
    print(f"Gross loss: {result['gross_loss']:.2f} USDT")
    print("Profit factor:", pf_text)
    print(f"Net P/L: {result['net']:.2f} USDT")
    print(f"Return: {result['net'] / START_BALANCE * 100:.2f}%")
    print(f"Max drawdown: {result['drawdown']:.2f}%")
    print(f"Final balance: {result['balance']:.2f} USDT")
    print(f"Average planned stop-risk at entry: {result['avg_actual_stop_risk_pct']:.3f}% of balance (before costs; not guaranteed loss cap)")
    print("Exit reasons:", result["reasons"])
    for side in ("long", "short"):
        pnl_list = result["sides"].get(side, [])
        if pnl_list:
            side_wins = sum(1 for p in pnl_list if p > 0)
            print(f"{side.upper()}: {len(pnl_list)} trades, "
                  f"{side_wins / len(pnl_list) * 100:.2f}% wins, "
                  f"net {sum(pnl_list):.2f} USDT")


def main():
    candles = get_candles()
    if len(candles) < 3000:
        print("ERROR: Not enough closed candles. Check the OKX API/data download.")
        return

    ind = calculate_indicators(candles)
    dev_end = int(len(candles) * DEV_FRACTION)
    val_end = int(len(candles) * (DEV_FRACTION + VALIDATION_FRACTION))
    print("\nTotal candles:", len(candles))
    print("Development (first 60%):", dev_end)
    print("Validation (next 20%):", val_end - dev_end)
    print("Final chronological holdout (last 20%):", len(candles) - val_end)
    print("\nAssumptions: risk target 1% before costs, but notional is capped at 1x balance; effective stop-risk can therefore be lower.")
    print("Fees/slippage are estimates per side; funding, liquidation, exchange lot sizing and contract-specific details are not modeled.")
    print("Signals use closed candles; entry is next candle open; if stop and target both touch, stop is assumed first.")
    print("ADX is a simple rolling approximation, not the platform's Wilder-smoothed ADX.")
    print("\nIMPORTANT: Keep the final 20% as a holdout. Do not pick the best filter by its holdout result.")

    for cfg in CONFIGS:
        signals = make_signals(candles, ind, cfg)
        print("\n" + "=" * 64)
        print("CONFIG:", cfg["name"], "| ADX minimum:", cfg.get("adx_min", 0),
              "| EMA separation / ATR minimum:", cfg.get("sep_atr_min", 0.0))
        dev = backtest(candles, signals, start=VOLUME_PERIOD + 3, end=dev_end)
        val = backtest(candles, signals, start=dev_end, end=val_end)
        holdout = backtest(candles, signals, start=val_end, end=len(candles) - 1)
        print_results("DEVELOPMENT - first 60%", dev)
        print_results("VALIDATION - next 20%", val)
        print_results("FINAL HOLDOUT - last 20% (do not optimize on this)", holdout)

    print("\nResearch only: a better result in this sample does not prove future profitability. If any candidate looks promising, test it in paper trading with realistic fees/funding before considering real money.")


if __name__ == "__main__":
    main()
