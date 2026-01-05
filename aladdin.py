# aladdin.py - Trading Bot
import time
import ccxt
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone, timedelta
import os
import sqlite3
import logging
import sys

log_formatter = logging.Formatter('%(asctime)s - %(message)s')
logger = logging.getLogger()
if logger.hasHandlers():
    logger.handlers.clear()
logger.setLevel(logging.INFO)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)
file_handler = logging.FileHandler("bot_output.log")
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)
con = sqlite3.connect('trading_bot.db', check_same_thread=False)
con.row_factory = sqlite3.Row
cur = con.cursor()


def initialize_database():
    cur.execute('''CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY, timestamp TEXT, pair TEXT, direction TEXT,
        entry_price REAL, quantity REAL, status TEXT, 
        stop_loss REAL, take_profit REAL, pnl REAL
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS bot_logs (id INTEGER PRIMARY KEY, timestamp TEXT, log_level TEXT, message TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS bot_status (key TEXT PRIMARY KEY, value TEXT, last_updated TEXT)''')
    cur.execute("INSERT OR IGNORE INTO bot_status (key, value) VALUES ('loss_limit_days_in_a_row', '0')")
    con.commit()


def prune_trade_history():
    """Keeps the trade history limited to the last MAX_TRADE_HISTORY records."""
    try:
        cur.execute("SELECT COUNT(*) FROM trades")
        count = cur.fetchone()[0]
        if count > MAX_TRADE_HISTORY:
            num_to_delete = count - MAX_TRADE_HISTORY
            cur.execute(f"SELECT id FROM trades ORDER BY id ASC LIMIT {num_to_delete}")
            ids_to_delete = cur.fetchall()
            delete_ids = [item['id'] for item in ids_to_delete]
            cur.executemany("DELETE FROM trades WHERE id = ?", [(id,) for id in delete_ids])
            con.commit()
    except Exception as e:
        logger.error(f"Error pruning trade history: {e}")


def prune_error_logs():
    """Keeps the error log history limited to the last MAX_ERROR_LOGS records."""
    try:
        cur.execute("SELECT COUNT(*) FROM bot_logs WHERE log_level IN ('ERROR', 'CRITICAL')")
        count = cur.fetchone()[0]
        if count > MAX_ERROR_LOGS:
            num_to_delete = count - MAX_ERROR_LOGS
            cur.execute(f"SELECT id FROM bot_logs WHERE log_level IN ('ERROR', 'CRITICAL') ORDER BY id ASC LIMIT {num_to_delete}")
            ids_to_delete = cur.fetchall()
            delete_ids = [item['id'] for item in ids_to_delete]
            cur.executemany("DELETE FROM bot_logs WHERE id = ?", [(id,) for id in delete_ids])
            con.commit()
    except Exception as e:
        logger.error(f"Error pruning error logs: {e}")


def log_status(level, message):
    try:
        cur.execute("INSERT INTO bot_logs (timestamp, log_level, message) VALUES (?, ?, ?)",
                    (datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), level, message))
        con.commit()
        if level in ('ERROR', 'CRITICAL'):
            prune_error_logs()
    except Exception as e:
        logger.error(f"Database logging failed: {e}")


def update_heartbeat():
    try:
        cur.execute("REPLACE INTO bot_status (key, value, last_updated) VALUES (?, ?, ?)",
                    ('heartbeat', 'running', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')))
        con.commit()
    except Exception as e:
        logger.error(f"Failed to update heartbeat: {e}")


exchange = ccxt.binance({'apiKey': os.getenv('BINANCE_API_KEY'), 'secret': os.getenv('BINANCE_API_SECRET'), 'enableRateLimit': True, 'options': {'defaultType': 'future'}})
PAIRS = ['SOL/USDT', 'LTC/USDT']
TIMEFRAME = '5m'
DEFAULT_LEVERAGE = 10
LEVERAGE_SETTINGS = {'LTC/USDT': 100, 'SOL/USDT': 100}
DAILY_PROFIT_TARGET = 0.06
MAX_OPEN_POSITIONS = 1
MAX_CONSECUTIVE_LOSSES = 3
API_MAX_RETRIES = 5
API_RETRY_DELAY = 5
MAX_ERROR_LOGS = 10
MAX_TRADE_HISTORY = 100
TRADE_COOLDOWN_MINUTES = 30

MARGIN_PCT_OF_CAPITAL = 0.02
RISK_PCT_OF_MARGIN = 0.50
REWARD_MULTIPLIER = 2

consecutive_losses = 0
paper_balance = 100.00
daily_starting_balance = paper_balance
profit_target_reached = False
consecutive_loss_limit_reached = False
last_trade_times = {}


def setup_leverage_and_mode():
    logger.info(f"Setting up leverage and margin mode for all pairs...")
    for pair in PAIRS:
        try:
            leverage = LEVERAGE_SETTINGS.get(pair, DEFAULT_LEVERAGE)
            exchange.set_margin_mode('isolated', symbol=pair)
            exchange.set_leverage(leverage, symbol=pair)
            logger.info(f"Setup complete for {pair}: {leverage}x leverage.")
        except Exception as e:
            logger.error(f"Could not set up {pair}. Error: {e}")


def fetch_ohlcv(symbol):
    try:
        return exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
    except Exception as e:
        logger.warning(f"Could not fetch OHLCV data for {symbol} due to API error: {e}")
        return None


# ALL 5 STRATEGIES
def strategy_ma_crossover(df):
    df['ma_fast'] = df['close'].rolling(window=13).mean()
    df['ma_slow'] = df['close'].rolling(window=48).mean()
    if df['ma_fast'].iloc[-2] < df['ma_slow'].iloc[-2] and df['ma_fast'].iloc[-1] > df['ma_slow'].iloc[-1]: return 'long'
    elif df['ma_fast'].iloc[-2] > df['ma_slow'].iloc[-2] and df['ma_fast'].iloc[-1] < df['ma_slow'].iloc[-1]: return 'short'
    return None


def strategy_rsi(df):
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=14).mean()
    if loss.iloc[-1] == 0: return None
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    if rsi.iloc[-1] < 30: return 'long'
    elif rsi.iloc[-1] > 70: return 'short'
    return None


def strategy_bollinger(df):
    ma = df['close'].rolling(window=20).mean()
    std = df['close'].rolling(window=20).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    if df['close'].iloc[-1] > upper.iloc[-1]: return 'long'
    elif df['close'].iloc[-1] < lower.iloc[-1]: return 'short'
    return None


def strategy_macd(df):
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    if macd.iloc[-2] < signal.iloc[-2] and macd.iloc[-1] > signal.iloc[-1]: return 'long'
    elif macd.iloc[-2] > signal.iloc[-2] and macd.iloc[-1] < signal.iloc[-1]: return 'short'
    return None


def strategy_breakout(df):
    recent_high = df['high'].iloc[-5:-2].max()
    recent_low = df['low'].iloc[-5:-2].min()
    if df['close'].iloc[-1] > recent_high: return 'long'
    elif df['close'].iloc[-1] < recent_low: return 'short'
    return None


def check_all_strategies(df):
    # Get signals from all strategies
    ma_signal = strategy_ma_crossover(df)
    rsi_signal = strategy_rsi(df)
    bollinger_signal = strategy_bollinger(df)
    macd_signal = strategy_macd(df)
    trend_signals = [ma_signal, macd_signal]
    momentum_signals = [rsi_signal, bollinger_signal]
    long_trend_confirm = trend_signals.count('long') >= 1
    short_trend_confirm = trend_signals.count('short') >= 1
    long_momentum_confirm = momentum_signals.count('long') >= 1
    short_momentum_confirm = momentum_signals.count('short') >= 1
    if long_trend_confirm and long_momentum_confirm:
        return 'long'
    elif short_trend_confirm and short_momentum_confirm:
        return 'short'
    return None


def execute_trade(pair, signal, df):
    try:
        entry_price = df['close'].iloc[-1]
        leverage = LEVERAGE_SETTINGS.get(pair, DEFAULT_LEVERAGE)
        margin_for_this_trade = paper_balance * MARGIN_PCT_OF_CAPITAL
        position_value = margin_for_this_trade * leverage
        position_size = position_value / entry_price
        if position_size <= 0:
            logger.warning(f"Skipping trade for {pair}: Invalid position size.")
            return

        risk_amount_dollars = margin_for_this_trade * RISK_PCT_OF_MARGIN
        profit_amount_dollars = risk_amount_dollars * REWARD_MULTIPLIER
        stop_loss_distance = risk_amount_dollars / position_size
        take_profit_distance = profit_amount_dollars / position_size

        if signal == 'long':
            stop_loss = entry_price - stop_loss_distance
            take_profit = entry_price + take_profit_distance
        else: 
            stop_loss = entry_price + stop_loss_distance
            take_profit = entry_price - take_profit_distance

        logger.info(f"ENTERING NEW TRADE: {signal.upper()} on {pair} at {entry_price}")
        trade_timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        cur.execute(
            "INSERT INTO trades (timestamp, pair, direction, entry_price, quantity, status, stop_loss, take_profit, pnl) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_timestamp, pair, signal, entry_price, position_size, 'open', stop_loss, take_profit, 0)
        )
        con.commit()
        prune_trade_history()
    except Exception as e:
        logger.error(f"Error executing trade for {pair}: {e}")


def manage_open_positions():
    global consecutive_losses, paper_balance, last_trade_times
    limit_was_hit = False
    open_trades = cur.execute("SELECT * FROM trades WHERE status = 'open'").fetchall()
    for trade in open_trades:
        try:
            latest_candle = fetch_ohlcv(trade['pair'])
            if not isinstance(latest_candle, list) or len(latest_candle) < 2: continue
            high_price = latest_candle[-1][2]
            low_price = latest_candle[-1][3]
            is_win = False
            is_loss = False

            if trade['direction'] == 'long':
                if low_price <= trade['stop_loss']: is_loss = True
                elif high_price >= trade['take_profit']: is_win = True
            elif trade['direction'] == 'short':
                if high_price >= trade['stop_loss']: is_loss = True
                elif low_price <= trade['take_profit']: is_win = True

            if is_win or is_loss:
                leverage = LEVERAGE_SETTINGS.get(trade['pair'], DEFAULT_LEVERAGE)
                margin_used = (trade['entry_price'] * trade['quantity']) / leverage
                risk_amount_dollars = margin_used * RISK_PCT_OF_MARGIN

                pnl = 0
                status = ""
                if is_win:
                    pnl = risk_amount_dollars * REWARD_MULTIPLIER
                    status = "win"
                    consecutive_losses = 0
                    logger.info(f"TRADE CLOSED (WIN): {trade['pair']}. PnL: ${pnl:.2f}")
                else:
                    pnl = -risk_amount_dollars
                    status = "loss"
                    consecutive_losses += 1
                    logger.warning(
                        f"TRADE CLOSED (LOSS): {trade['pair']}. PnL: ${pnl:.2f}. Consecutive losses: {consecutive_losses}")

                paper_balance += pnl
                logger.info(f"New paper balance: ${paper_balance:.2f}")
                cur.execute("UPDATE trades SET status = ?, pnl = ? WHERE id = ?", (status, pnl, trade['id']))
                con.commit()
                last_trade_times[trade['pair']] = datetime.now(timezone.utc)
                if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                    limit_was_hit = True
        except Exception as e:
            logger.error(f"Error managing open position for {trade['pair']}: {e}")
    return limit_was_hit


def is_trending_market(df, adx_threshold=25):
    """Checks if the market has a strong trend using the ADX indicator."""
    try:
        adx_series = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_series is None or adx_series.empty or 'ADX_14' not in adx_series.columns:
            logger.warning("Could not calculate ADX series.")
            return False 

        last_adx = adx_series.iloc[-1]['ADX_14']

        logger.info(f"ADX Check for {df.index[-1]}: Current ADX is {last_adx:.2f} (Threshold: >{adx_threshold})")

        return last_adx > adx_threshold
    except Exception as e:
        logger.error(f"Error calculating ADX: {e}")
        return False


def is_trend_confirmed(df, signal, ema_period=21):
    """Confirms the signal with a 21-period EMA filter."""
    try:
        df['ema21'] = df['close'].ewm(span=ema_period, adjust=False).mean()
        last_close = df['close'].iloc[-1]
        last_ema = df['ema21'].iloc[-1]

        if signal == 'long' and last_close > last_ema:
            return True
        if signal == 'short' and last_close < last_ema:
            return True
        
        logger.info(f"Signal '{signal}' for {df.index[-1]} rejected by 21 EMA filter (Price: {last_close:.2f}, EMA: {last_ema:.2f})")
        return False
    except Exception as e:
        logger.error(f"Error in EMA trend confirmation: {e}")
        return False


def run_bot():
    global paper_balance, daily_starting_balance, profit_target_reached, consecutive_loss_limit_reached, last_trade_times
    initialize_database()
    logger.info("Starting up Aladdin...")
    try:
        exchange.load_markets()
    except Exception as e:
        logger.critical(f"CRITICAL ERROR on startup: {e}. Bot cannot start.")
        sys.exit(1)

    setup_leverage_and_mode()
    last_checked_day = datetime.now(timezone.utc).day
    daily_starting_balance = paper_balance
    loss_limit_days_row = cur.execute("SELECT value FROM bot_status WHERE key = 'loss_limit_days_in_a_row'").fetchone()
    loss_limit_days_in_a_row = int(loss_limit_days_row['value']) if loss_limit_days_row else 0
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            if now_utc.day != last_checked_day:
                logger.info("It's a new day! Resetting all daily limits.")
                if not consecutive_loss_limit_reached:
                    loss_limit_days_in_a_row = 0
                    cur.execute("UPDATE bot_status SET value = '0' WHERE key = 'loss_limit_days_in_a_row'")
                    con.commit()
                profit_target_reached = False
                consecutive_loss_limit_reached = False
                daily_starting_balance = paper_balance
                last_checked_day = now_utc.day

            if now_utc.minute % 5 == 0:
                update_heartbeat()
                loss_limit_hit_this_cycle = manage_open_positions()
                if not consecutive_loss_limit_reached and loss_limit_hit_this_cycle:
                    consecutive_loss_limit_reached = True
                    log_message = "Aladdin took 3 loose trades in a row"
                    logger.info(log_message)
                    log_message_2 = "Aladdin has stopped working for today"
                    logger.info(log_message_2)
                    loss_limit_days_in_a_row += 1
                    cur.execute("UPDATE bot_status SET value = ? WHERE key = 'loss_limit_days_in_a_row'",
                                (str(loss_limit_days_in_a_row),))
                    con.commit()
                    if loss_limit_days_in_a_row >= 3:
                        logger.critical("STOPPED FOR 3 CONSECUTIVE DAYS. BOT IS SHUTTING DOWN PERMANENTLY.")
                        sys.exit(0)

                if not profit_target_reached:
                    current_profit_pct = (paper_balance - daily_starting_balance) / daily_starting_balance if daily_starting_balance > 0 else 0
                    if current_profit_pct >= DAILY_PROFIT_TARGET:
                        profit_target_reached = True
                        log_message = f"Target reached... Aladdin stopped until 00:00 UTC"
                        logger.info(log_message)

                if profit_target_reached or consecutive_loss_limit_reached:
                    time.sleep(60)
                    continue

                open_positions_count = len(cur.execute("SELECT id FROM trades WHERE status = 'open'").fetchall())
                logger.info(f"\nChecking for signals... (Open Positions: {open_positions_count}/{MAX_OPEN_POSITIONS})")
                if open_positions_count >= MAX_OPEN_POSITIONS:
                    time.sleep(60)
                    continue

                for pair in PAIRS:
                    if len(cur.execute("SELECT id FROM trades WHERE status = 'open'").fetchall()) >= MAX_OPEN_POSITIONS: break
                    if cur.execute("SELECT id FROM trades WHERE status = 'open' AND pair = ?", (pair,)).fetchone(): continue

                    if pair in last_trade_times:
                        time_since_last_trade = now_utc - last_trade_times[pair]
                        if time_since_last_trade < timedelta(minutes=TRADE_COOLDOWN_MINUTES):
                            logger.info(f"Pair {pair} is in cooldown. Skipping.")
                            continue

                    ohlcv = None
                    for i in range(API_MAX_RETRIES):
                        fetched_data = fetch_ohlcv(pair)
                        if isinstance(fetched_data, list) and len(fetched_data) > 0:
                            ohlcv = fetched_data
                            break
                        else:
                            time.sleep(API_RETRY_DELAY)
                    if ohlcv is None:
                        logger.error(f"Failed to fetch data for {pair}. Skipping.")
                        continue
                    try:
                        df = pd.DataFrame(ohlcv,
                                          columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        df.set_index(pd.to_datetime(df['timestamp'], unit='ms'), inplace=True)
                        if is_trending_market(df):
                            signal = check_all_strategies(df)
                            if signal and is_trend_confirmed(df, signal):
                                execute_trade(pair, signal, df)
                    except Exception as e:
                        logger.error(f"Error processing signal for {pair}: {e}")
                time.sleep(60)
            time.sleep(10)
        except KeyboardInterrupt:
            logger.info("Bot stopped manually.")
            break
        except Exception as e:
            logger.critical(f"An unexpected error occurred in main loop: {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_bot()