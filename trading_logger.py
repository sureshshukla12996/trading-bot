"""
Logging system for the Binance Futures Trading Bot.
Provides structured logging for trades, errors, and performance metrics.
"""

import logging
import os
import json
from datetime import datetime, timezone
from typing import Optional

from config import Config


def _ensure_log_dir() -> None:
    """Create the log directory if it does not exist."""
    os.makedirs(Config.LOG_DIR, exist_ok=True)


def get_logger(name: str = "trading_bot") -> logging.Logger:
    """
    Return a configured :class:`logging.Logger` instance.

    The logger writes to both the console and a rotating log file.
    Calling this multiple times with the same *name* returns the same logger.
    """
    _ensure_log_dir()

    logger = logging.getLogger(name)
    if logger.handlers:
        # Already configured – return as-is to avoid duplicate handlers.
        return logger

    log_level = getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(log_level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    # File handler
    file_handler = logging.FileHandler(Config.LOG_FILE, encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


class TradeLogger:
    """
    Structured trade logger.

    Records trade events (open, close, update) as JSON lines in a dedicated
    trade-history file, in addition to the standard application log.
    """

    TRADE_LOG_FILE = os.path.join(Config.LOG_DIR, "trade_history.jsonl")

    def __init__(self) -> None:
        _ensure_log_dir()
        self._logger = get_logger("trade_logger")

    # ── Internal helpers ────────────────────────────────────────────────────

    def _append(self, record: dict) -> None:
        """Append *record* as a JSON line to the trade-history file."""
        record["timestamp"] = datetime.now(tz=timezone.utc).isoformat()
        with open(self.TRADE_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    # ── Public API ───────────────────────────────────────────────────────────

    def log_trade_open(
        self,
        symbol: str,
        side: str,
        leverage: int,
        entry_price: float,
        quantity: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        order_id: Optional[str] = None,
    ) -> None:
        """Log a newly opened trade position."""
        record = {
            "event": "TRADE_OPEN",
            "symbol": symbol,
            "side": side,
            "leverage": leverage,
            "entry_price": entry_price,
            "quantity": quantity,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "order_id": order_id,
        }
        self._append(record)
        self._logger.info(
            "OPEN  | %s %s | entry=%.4f | qty=%.4f | lev=%dx | sl=%s | tp=%s",
            side,
            symbol,
            entry_price,
            quantity,
            leverage,
            stop_loss,
            take_profit,
        )

    def log_trade_close(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        pnl: float,
        reason: str = "manual",
    ) -> None:
        """Log a closed trade with P&L."""
        record = {
            "event": "TRADE_CLOSE",
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl": pnl,
            "reason": reason,
        }
        self._append(record)
        self._logger.info(
            "CLOSE | %s %s | entry=%.4f | exit=%.4f | qty=%.4f | pnl=%.4f | reason=%s",
            side,
            symbol,
            entry_price,
            exit_price,
            quantity,
            pnl,
            reason,
        )

    def log_position_update(
        self,
        symbol: str,
        new_sl: Optional[float],
        new_tp: Optional[float],
    ) -> None:
        """Log a stop-loss / take-profit update."""
        record = {
            "event": "POSITION_UPDATE",
            "symbol": symbol,
            "new_stop_loss": new_sl,
            "new_take_profit": new_tp,
        }
        self._append(record)
        self._logger.info("UPDATE| %s | sl=%s | tp=%s", symbol, new_sl, new_tp)

    def log_error(self, context: str, error: Exception) -> None:
        """Log an error with context."""
        record = {
            "event": "ERROR",
            "context": context,
            "error": str(error),
            "error_type": type(error).__name__,
        }
        self._append(record)
        self._logger.error("ERROR | %s | %s: %s", context, type(error).__name__, error)

    def log_alert(self, message: str, alert_type: str = "INFO") -> None:
        """Log an outgoing alert."""
        record = {
            "event": "ALERT",
            "alert_type": alert_type,
            "message": message,
        }
        self._append(record)
        self._logger.info("ALERT | [%s] %s", alert_type, message)

    def log_performance(self, metrics: dict) -> None:
        """Log a performance snapshot."""
        record = {"event": "PERFORMANCE", **metrics}
        self._append(record)
        self._logger.info("PERF  | %s", json.dumps(metrics))

    # ── Report helpers ───────────────────────────────────────────────────────

    def get_trade_history(self) -> list:
        """Return all recorded trade events as a list of dicts."""
        if not os.path.exists(self.TRADE_LOG_FILE):
            return []
        history = []
        with open(self.TRADE_LOG_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        history.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return history

    def generate_trade_report(self) -> dict:
        """
        Generate a summary report from the trade history.

        Returns a dict with keys: total_trades, winning_trades, losing_trades,
        win_rate, total_pnl, average_pnl.
        """
        history = self.get_trade_history()
        close_events = [e for e in history if e.get("event") == "TRADE_CLOSE"]

        if not close_events:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "average_pnl": 0.0,
            }

        pnls = [e["pnl"] for e in close_events]
        winning = [p for p in pnls if p > 0]
        losing = [p for p in pnls if p <= 0]

        return {
            "total_trades": len(pnls),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": round(len(winning) / len(pnls) * 100, 2) if pnls else 0.0,
            "total_pnl": round(sum(pnls), 4),
            "average_pnl": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
        }
