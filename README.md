# 🧞‍♂️ Aladdin: Paper Trading Simulation Suite
**An institutional-grade strategy development lab for risk-free crypto algorithmic trading.**

**Aladdin** is a high-performance simulation engine designed to replicate live market dynamics for the Binance Futures market without financial risk. It provides a comprehensive "sandbox" for developers to test multi-strategy consensus models, manage virtual capital, and monitor real-time telemetry through a dedicated web dashboard.

---

## 🔬 The Strategy Simulation Lab

### 1. Multi-Indicator Consensus Engine (`aladdin.py`)
Aladdin moves beyond single-signal trading by requiring a weighted consensus before triggering a paper trade:
* **Trend Confirmation**: Aligns MA Crossover and MACD signals.
* **Momentum Verification**: Cross-references RSI and Bollinger Bands.
* **Market State Filter**: Uses the ADX (Average Directional Index) to ensure simulations only run in strong trending environments.
* **Gatekeeper Logic**: A 21-period EMA filter acts as a final barrier to verify entry precision.

### 2. Real-Time Telemetry Dashboard (`dashboard.py`)
Analyze your strategy's performance through a modern Flask-powered interface:
* **Interactive Control**: Toggle the simulation engine instantly via the web UI.
* **Live Feed**: Stream engine logs to observe decision-making logic in real-time.
* **Deep Analytics**: Automatically tracks virtual win rates, daily PnL, and simulated trade history.

---

## 🛡️ Educational Risk Controls
Aladdin prioritizes "Capital Preservation Education" by enforcing strict simulated risk protocols:
* **Simulation Target (6%)**: Automatically ceases operations for the day once the target virtual profit is hit.
* **The "3-Loss" Killswitch**: Automatically halts the bot for 24 hours after 3 consecutive paper losses to simulate discipline.
* **Isolated Margin Mode**: Educates users on isolated margin management with customizable simulated leverage (up to 100x).
* **Strategic Cooldown**: Enforces a 30-minute mandatory break between trades to prevent simulated "revenge trading".

---

## ⚙️ Technical Stack
* **Backend**: Python 3.10+
* **Market Connectivity**: CCXT (Binance Futures)
* **Database Management**: SQLite3 for persistent simulation logs
* **Server Architecture**: Flask + Waitress (Production WSGI)

---

## 📦 Getting Started

### 1. Environment Setup
```bash
git clone [https://github.com/your-username/Aladdin-paper-trading-bot.git](https://github.com/your-username/Aladdin-paper-trading-bot.git)
cd Aladdin-paper-trading-bot
pip install -r requirements.txt
