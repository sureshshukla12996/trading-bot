# Binance Futures Trading Bot

A comprehensive Python trading bot for Binance USDT-margined perpetual futures with advanced risk management, automated signal generation, and real-time monitoring.

## Features

- **USDT Pairs Only** – automatically filters and trades only USDT-margined futures
- **Technical Analysis** – MA, RSI, MACD, Bollinger Bands, Volume Profile
- **Signal Generation** – automated LONG / SHORT signals with leverage recommendations
- **Position Management** – open, close, update stop-loss / take-profit
- **Leverage Control** – 1x to 125x (configurable cap)
- **Risk Management** – max drawdown protection, margin monitoring, position sizing
- **Grid Trading** – multi-level entry / exit grids
- **Position Scaling** – add capital to winning trades
- **Auto-Reentry** – retry failed trades with signal confirmation
- **Portfolio Diversification** – auto-allocate across top 10 USDT pairs
- **Backtesting Engine** – test strategy on historical kline data
- **Parameter Optimisation** – grid-search over leverage values
- **Performance Reports** – daily and monthly summaries
- **Telegram Alerts** – trade opened/closed, margin warnings, liquidation risk
- **Comprehensive Logging** – structured JSON trade history + rotating log file
- **Testnet Support** – develop and test without risking real funds

## File Structure

```
trading-bot/
├── trading_bot_futures.py   # Main bot implementation (25+ functions)
├── config.py                # Configuration management
├── trading_logger.py        # Structured logging system
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variables template
├── tests/
│   ├── test_futures.py      # Unit tests for bot functions
│   └── test_indicators.py   # Unit tests for technical indicators
├── logs/
│   └── trades.log           # Application log (auto-created)
└── README.md
```

## Quick Start

### 1 – Install dependencies

```bash
pip install -r requirements.txt
```

### 2 – Configure environment variables

```bash
cp .env.example .env
# Edit .env and add your Binance API keys, Telegram token, etc.
```

### 3 – Run the bot

```python
from trading_bot_futures import TradingBot

# Initialise in testnet mode (recommended for first use)
bot = TradingBot(testnet=True)

# Fetch all active USDT futures symbols
symbols = bot.fetch_futures_symbols()
print(symbols[:5])  # ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', ...]

# Execute a single LONG trade
trade = bot.execute_futures_trade(
    symbol="BTCUSDT",
    side="LONG",
    leverage=10,
    amount=100,        # USDT notional
    stop_loss=0.02,    # 2 % stop-loss
    take_profit=0.04,  # 4 % take-profit
)

# Check open positions
positions = bot.get_open_positions()

# Start automated trading loop (requires AUTO_TRADING=true in .env)
bot.start_trading()
```

## Usage Examples

### Grid Trading

```python
result = bot.execute_grid_trade(
    symbol="BTCUSDT",
    side="LONG",
    entry_price=45000,
    grid_levels=5,
    grid_spacing=0.01,   # 1 % spacing between levels
)
```

### Portfolio Allocation

```python
# Distribute 1000 USDT across top 10 USDT pairs at 5x leverage
result = bot.allocate_futures_capital(total_amount=1000, leverage=5)
```

### Backtesting

```python
raw = bot.get_futures_market_data("BTCUSDT", interval="1h")
result = bot.backtest_futures_strategy("BTCUSDT", raw["klines"], leverage=10)
print(result["roi"], result["win_rate"], result["max_drawdown"])
```

### Performance Tracking

```python
report = bot.track_futures_performance()
# {total_trades, winning_trades, win_rate, total_pnl, roi, max_drawdown, ...}

bot.generate_daily_report()   # also sends Telegram summary
bot.generate_monthly_report()
```

## Configuration

All settings are controlled via environment variables (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `BINANCE_API_KEY` | – | Binance API key |
| `BINANCE_API_SECRET` | – | Binance API secret |
| `BINANCE_FUTURES_TESTNET` | `true` | Use testnet endpoint |
| `TELEGRAM_BOT_TOKEN` | – | Telegram bot token |
| `TELEGRAM_CHAT_ID` | – | Telegram chat ID |
| `MAX_LEVERAGE` | `10` | Maximum allowed leverage |
| `MAX_DRAWDOWN` | `10` | Max drawdown % before halting |
| `RISK_PER_TRADE` | `2` | Capital at risk per trade (%) |
| `AUTO_TRADING` | `false` | Enable automated trading loop |
| `TOP_N_SYMBOLS` | `10` | Symbols for portfolio allocation |

## Running Tests

```bash
python -m pytest tests/ -v
```

## Safety

> ⚠️ **Always test on the Binance Futures Testnet before using real funds.**
> Futures trading with leverage carries significant risk of loss, including liquidation of your entire position.

- Set `BINANCE_FUTURES_TESTNET=true` during development
- Start with low leverage (1x–5x)
- Use `MAX_DRAWDOWN` to limit daily losses
- Monitor margin levels via `check_margin_level()`
- Review liquidation prices with `get_liquidation_price()` before entering trades

## License

MIT
