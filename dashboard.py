# dashboard - A Dashboard for the Aladdin Bot

from flask import Flask, render_template, jsonify
import sqlite3
from datetime import datetime, timedelta
import subprocess
import os
import signal
from waitress import serve

app = Flask(__name__)

DATABASE_FILE = 'trading_bot.db'
BOT_SCRIPT_FILE = 'aladdin.py'
PID_FILE = 'bot.pid'
LOG_FILE = 'bot_output.log'

def is_bot_running():
    if not os.path.exists(PID_FILE): return False
    with open(PID_FILE, 'r') as f: pid = f.read().strip()
    if not pid: return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        os.remove(PID_FILE); return False
    return True

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_bot', methods=['POST'])
def start_bot():
    if is_bot_running():
        return jsonify(status="error", message="Bot is already running.")
    try:
        if os.path.exists(LOG_FILE): os.remove(LOG_FILE)
        process = subprocess.Popen(['python3', BOT_SCRIPT_FILE])
        with open(PID_FILE, 'w') as f: f.write(str(process.pid))
        return jsonify(status="success", message="Bot started successfully.")
    except Exception as e:
        return jsonify(status="error", message=f"Failed to start bot: {str(e)}")

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    if not is_bot_running():
        return jsonify(status="error", message="Bot is not running.")
    try:
        with open(PID_FILE, 'r') as f: pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        os.remove(PID_FILE)
        return jsonify(status="success", message="Bot stopped successfully.")
    except Exception as e:
        if os.path.exists(PID_FILE): os.remove(PID_FILE)
        return jsonify(status="error", message=f"Failed to stop bot: {str(e)}")

@app.route('/api/live_output')
def api_live_output():
    try:
        with open(LOG_FILE, 'r') as f: lines = f.readlines()
        return jsonify(output=''.join(lines[-30:]))
    except Exception:
        return jsonify(output="Log file not found or is empty.")

@app.route('/api/status')
def api_status():
    status_info = {"status": "Stopped", "last_heartbeat": "N/A"}
    if is_bot_running():
        conn = get_db_connection()
        try:
            heartbeat_data = conn.execute("SELECT last_updated FROM bot_status WHERE key = 'heartbeat'").fetchone()
            if heartbeat_data:
                last_heartbeat_utc = datetime.strptime(heartbeat_data['last_updated'], '%Y-%m-%d %H:%M:%S')
                status_info['status'] = "Running" if datetime.utcnow() - last_heartbeat_utc < timedelta(minutes=7) else "Stalled"
                status_info['last_heartbeat'] = heartbeat_data['last_updated'] + " UTC"
            else:
                 status_info['status'] = "Starting..."
        except Exception as e:
            status_info['status'] = "Error"; status_info['last_heartbeat'] = str(e)
        conn.close()
    return jsonify(status=status_info)

@app.route('/api/performance')
def api_performance():
    conn = get_db_connection()
    wins = conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'win'").fetchone()[0]
    losses = conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'loss'").fetchone()[0]
    total_trades = wins + losses
    win_percentage = (wins / total_trades * 100) if total_trades > 0 else 0
    pnl_today_utc = conn.execute("SELECT SUM(pnl) FROM trades WHERE status IN ('win', 'loss') AND date(timestamp) = date('now', 'utc')").fetchone()[0] or 0
    pnl_this_month_utc = conn.execute("SELECT SUM(pnl) FROM trades WHERE status IN ('win', 'loss') AND strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now', 'utc')").fetchone()[0] or 0
    pnl_this_year_utc = conn.execute("SELECT SUM(pnl) FROM trades WHERE status IN ('win', 'loss') AND strftime('%Y', timestamp) = strftime('%Y', 'now', 'utc')").fetchone()[0] or 0
    conn.close()
    return jsonify(
        win_percentage=win_percentage, total_trades=total_trades, wins=wins, losses=losses,
        pnl_today=pnl_today_utc, pnl_this_month=pnl_this_month_utc, pnl_this_year=pnl_this_year_utc,
    )

@app.route('/api/trades')
def api_trades():
    conn = get_db_connection()
    trades = []
    try:
        trade_data = conn.execute("SELECT timestamp, pair, direction, entry_price, status, pnl FROM trades ORDER BY id DESC LIMIT 100").fetchall()
        trades = [dict(row) for row in trade_data]
    except Exception: pass
    conn.close()
    return jsonify(trades)

@app.route('/api/error_logs')
def api_error_logs():
    conn = get_db_connection()
    errors = []
    try:
        error_data = conn.execute("SELECT timestamp, message FROM bot_logs WHERE log_level IN ('ERROR', 'CRITICAL') ORDER BY id DESC LIMIT 10").fetchall()
        errors = [f"[{row['timestamp']}] {row['message']}" for row in error_data]
    except Exception: pass
    conn.close()
    return jsonify(errors=errors)

if __name__ == '__main__':
    print("Starting production server with Waitress...")
    serve(app, host='0.0.0.0', port=5000)