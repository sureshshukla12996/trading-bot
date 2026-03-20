"""
Configuration management for the Binance Futures Trading Bot.
Loads settings from environment variables with sensible defaults.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Central configuration for the trading bot."""

    # ── Binance API ──────────────────────────────────────────────────────────
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
    BINANCE_FUTURES_TESTNET: bool = os.getenv("BINANCE_FUTURES_TESTNET", "true").lower() == "true"

    # Endpoints (testnet vs live)
    FUTURES_BASE_URL: str = (
        "https://testnet.binancefuture.com"
        if BINANCE_FUTURES_TESTNET
        else "https://fapi.binance.com"
    )

    # ── Telegram ─────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # ── News API ─────────────────────────────────────────────────────────────
    NEWS_API_KEY: str = os.getenv("NEWS_API_KEY", "")
    NEWS_API_URL: str = "https://newsapi.org/v2/everything"

    # ── Trading Parameters ───────────────────────────────────────────────────
    MAX_LEVERAGE: int = int(os.getenv("MAX_LEVERAGE", "10"))
    MIN_LEVERAGE: int = 1
    ABSOLUTE_MAX_LEVERAGE: int = 125

    DEFAULT_LEVERAGE: int = int(os.getenv("DEFAULT_LEVERAGE", "5"))

    # ── Risk Management ───────────────────────────────────────────────────────
    MAX_DRAWDOWN: float = float(os.getenv("MAX_DRAWDOWN", "10"))          # percent
    RISK_PER_TRADE: float = float(os.getenv("RISK_PER_TRADE", "2"))       # percent of capital
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "10"))

    # Default stop-loss / take-profit as fractions of entry price
    DEFAULT_STOP_LOSS: float = float(os.getenv("DEFAULT_STOP_LOSS", "0.02"))   # 2 %
    DEFAULT_TAKE_PROFIT: float = float(os.getenv("DEFAULT_TAKE_PROFIT", "0.04"))  # 4 %

    # ── Portfolio ─────────────────────────────────────────────────────────────
    TOP_N_SYMBOLS: int = int(os.getenv("TOP_N_SYMBOLS", "10"))

    # ── Backtesting ───────────────────────────────────────────────────────────
    BACKTEST_INITIAL_CAPITAL: float = float(os.getenv("BACKTEST_INITIAL_CAPITAL", "10000"))

    # ── Auto-Trading ─────────────────────────────────────────────────────────
    AUTO_TRADING: bool = os.getenv("AUTO_TRADING", "false").lower() == "true"

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")
    LOG_FILE: str = os.path.join(LOG_DIR, "trades.log")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Default notional trade amount in USDT for auto-reentry and auto-trading
    DEFAULT_TRADE_AMOUNT: float = float(os.getenv("DEFAULT_TRADE_AMOUNT", "100"))

    # ── Grid Trading Defaults ─────────────────────────────────────────────────
    DEFAULT_GRID_LEVELS: int = int(os.getenv("DEFAULT_GRID_LEVELS", "5"))
    DEFAULT_GRID_SPACING: float = float(os.getenv("DEFAULT_GRID_SPACING", "0.01"))  # 1 %

    # ── Auto-Reentry ──────────────────────────────────────────────────────────
    MAX_REENTRY_RETRIES: int = int(os.getenv("MAX_REENTRY_RETRIES", "3"))

    @classmethod
    def validate(cls) -> list:
        """Return a list of configuration warnings (missing critical values)."""
        warnings = []
        if not cls.BINANCE_API_KEY:
            warnings.append("BINANCE_API_KEY is not set")
        if not cls.BINANCE_API_SECRET:
            warnings.append("BINANCE_API_SECRET is not set")
        if not cls.TELEGRAM_BOT_TOKEN:
            warnings.append("TELEGRAM_BOT_TOKEN is not set – alerts disabled")
        if not cls.TELEGRAM_CHAT_ID:
            warnings.append("TELEGRAM_CHAT_ID is not set – alerts disabled")
        return warnings
