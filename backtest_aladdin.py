# backtest_aladdin.py
import pandas as pd
import ccxt
import os
import time
from datetime import datetime, timezone
from aladdin import check_all_strategies, is_trend_confirmed, is_trending_market

def timeframe_to_ms(tf: str) -> int:
    mul = int(tf[:-1])
    unit = tf[-1].lower()
    if unit == 'm': return mul * 60_000
    if unit == 'h': return mul * 60 * 60_000
    if unit == 'd': return mul * 24 * 60 * 60_000
    raise ValueError(f"Unsupported timeframe: {tf}")

def fetch_many_candles(symbol, timeframe, total_limit=50_000, batch_size=1500):
    """
    Fetch candles from Binance Futures by paginating FORWARD in time from
    a point in the past so we truly load total_limit candles.
    """
    exchange = ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })

    tf_ms = timeframe_to_ms(timeframe)
    end_ms = exchange.milliseconds()
    start_ms = end_ms - total_limit * tf_ms

    all_candles = []
    since = start_ms
    print(f"Fetching {total_limit} candles for {symbol} ({timeframe}) from {datetime.utcfromtimestamp(since/1000)} UTC ...")

    while len(all_candles) < total_limit:
        left = total_limit - len(all_candles)
        limit = min(batch_size, left)
        candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        if not candles:
            break

        all_candles.extend(candles)
        since = candles[-1][0] + tf_ms
        time.sleep(exchange.rateLimit / 1000.0)

        if len(all_candles) % (batch_size*3) < batch_size:
            print(f"  ...loaded {len(all_candles)} / {total_limit}")

        if len(candles) < limit:
            break

    df = pd.DataFrame(all_candles, columns=['ts','open','high','low','close','vol'])
    if df.empty:
        raise RuntimeError("No candles returned. Try lowering total_limit or check symbol/timeframe.")
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df.set_index('ts', inplace=True)
    if len(df) > total_limit:
        df = df.iloc[-total_limit:]
    return df

def load_or_fetch_data(symbol, timeframe, total_limit=50_000):
    """
    Cache candles to CSV so repeated backtests are instant.
    """
    fname = f"{symbol.replace('/','_')}_{timeframe}_{total_limit}.csv"
    if os.path.exists(fname):
        print(f"Loading data from {fname} ...")
        df = pd.read_csv(fname, parse_dates=['ts'])
        df['ts'] = pd.to_datetime(df['ts'], utc=True)
        df.set_index('ts', inplace=True)
    else:
        df = fetch_many_candles(symbol, timeframe, total_limit=total_limit)
        df.to_csv(fname, index=True)
        print(f"Saved data to {fname}")
    return df

def backtest_aladdin(symbol="LTC/USDT", timeframe="5m", total_limit=50_000,
                     starting_balance=100.0, leverage=100,
                     margin_pct=0.02, risk_pct=0.5, reward_mult=2):
    df = load_or_fetch_data(symbol, timeframe, total_limit)
    print(f"Data loaded: {len(df)} candles.")
    print(f"Coverage: {df.index[0]}  ->  {df.index[-1]}  (UTC)")

    balance = starting_balance
    trades = []
    open_trade = None

    warmup = 50
    for i in range(warmup, len(df) - 1):
        history = df.iloc[:i+1]
        current = df.iloc[i]

        if open_trade:
            if open_trade['side'] == 'long':
                if current['low'] <= open_trade['sl']:
                    balance -= open_trade['risk']
                    trades.append(-open_trade['risk'])
                    open_trade = None
                elif current['high'] >= open_trade['tp']:
                    balance += open_trade['risk'] * reward_mult
                    trades.append(open_trade['risk'] * reward_mult)
                    open_trade = None
            else:  # short
                if current['high'] >= open_trade['sl']:
                    balance -= open_trade['risk']
                    trades.append(-open_trade['risk'])
                    open_trade = None
                elif current['low'] <= open_trade['tp']:
                    balance += open_trade['risk'] * reward_mult
                    trades.append(open_trade['risk'] * reward_mult)
                    open_trade = None

        if not open_trade:
            if is_trending_market(history):
                signal = check_all_strategies(history)
                if signal and is_trend_confirmed(history, signal):
                    entry = df.iloc[i + 1]['open']
                    margin = balance * margin_pct
                    pos_value = margin * leverage
                    pos_size = pos_value / entry
                    risk_dollars = margin * risk_pct
                    if pos_size <= 0 or risk_dollars <= 0:
                        continue
                    stop_dist = risk_dollars / pos_size
                    tp_dist = (risk_dollars * reward_mult) / pos_size
                    if signal == 'long':
                        sl, tp = entry - stop_dist, entry + tp_dist
                    else:
                        sl, tp = entry + stop_dist, entry - tp_dist
                    open_trade = {"side": signal, "entry": entry, "sl": sl, "tp": tp, "risk": risk_dollars}

    wins = sum(1 for t in trades if t > 0)
    losses = sum(1 for t in trades if t < 0)
    win_rate = (wins / len(trades) * 100) if trades else 0.0

    print(f"\n=== Backtest Results for {symbol} ({timeframe}) ===")
    print(f"Trades taken: {len(trades)}")
    print(f"Wins: {wins} | Losses: {losses}")
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Final Balance: ${balance:.2f} (Start: ${starting_balance})")

if __name__ == "__main__":
    backtest_aladdin("SOL/USDT", "5m", total_limit=60_000, reward_mult=2)