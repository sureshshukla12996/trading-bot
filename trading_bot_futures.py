"""
Binance Futures Trading Bot – Main Implementation
==================================================
Supports USDT-margined perpetual futures on Binance (live & testnet).

All public methods handle their own exceptions and return structured dicts
so that callers can always inspect a ``"status"`` / ``"error"`` field.
"""

from __future__ import annotations

import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests
import pandas as pd
import numpy as np

from config import Config
from trading_logger import TradeLogger, get_logger

logger: logging.Logger = get_logger("trading_bot_futures")
trade_logger = TradeLogger()


# ─────────────────────────────────────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────────────────────────────────────

def _ts() -> str:
    """Return current UTC timestamp as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _pct(value: float, reference: float) -> float:
    """Return percentage change of *value* relative to *reference*."""
    if reference == 0:
        return 0.0
    return round((value - reference) / reference * 100, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Main Bot Class
# ─────────────────────────────────────────────────────────────────────────────

class TradingBot:
    """
    Binance Futures trading bot supporting USDT-margined perpetual contracts.

    Parameters
    ----------
    testnet:
        When *True* the bot targets the Binance Futures testnet.
        When *False* it targets the live exchange.
    """

    def __init__(self, testnet: bool = True) -> None:
        self.testnet = testnet
        self.base_url = (
            "https://testnet.binancefuture.com"
            if testnet
            else "https://fapi.binance.com"
        )
        self.api_key = Config.BINANCE_API_KEY
        self.api_secret = Config.BINANCE_API_SECRET

        # In-memory position / state stores
        self._positions: dict[str, dict] = {}   # symbol → position dict
        self._leverages: dict[str, int] = {}     # symbol → current leverage
        self._daily_start_balance: float = 0.0
        self._trade_history: list[dict] = []
        self._grid_positions: dict[str, list] = {}  # symbol → list of grid orders

        config_warnings = Config.validate()
        for w in config_warnings:
            logger.warning("Config warning: %s", w)

        logger.info("TradingBot initialised | testnet=%s | url=%s", testnet, self.base_url)

    # ── Low-level HTTP ───────────────────────────────────────────────────────

    def _get(self, path: str, params: Optional[dict] = None, signed: bool = False) -> dict:
        """Make an authenticated GET request to the Futures REST API."""
        url = self.base_url + path
        headers = {"X-MBX-APIKEY": self.api_key}
        try:
            resp = requests.get(url, params=params or {}, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            trade_logger.log_error(f"GET {path}", exc)
            raise

    def _post(self, path: str, data: Optional[dict] = None) -> dict:
        """Make an authenticated POST request to the Futures REST API."""
        url = self.base_url + path
        headers = {"X-MBX-APIKEY": self.api_key}
        try:
            resp = requests.post(url, data=data or {}, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            trade_logger.log_error(f"POST {path}", exc)
            raise

    # ════════════════════════════════════════════════════════════════════════
    # 1.  MARKET DATA
    # ════════════════════════════════════════════════════════════════════════

    def fetch_futures_symbols(self) -> list[str]:
        """
        Fetch all actively trading USDT-margined futures symbols.

        Returns
        -------
        list[str]
            E.g. ``['BTCUSDT', 'ETHUSDT', ...]``
        """
        try:
            data = self._get("/fapi/v1/exchangeInfo")
            symbols = [
                s["symbol"]
                for s in data.get("symbols", [])
                if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
            ]
            logger.info("Fetched %d USDT futures symbols", len(symbols))
            return symbols
        except Exception as exc:
            trade_logger.log_error("fetch_futures_symbols", exc)
            return []

    def get_futures_market_data(self, symbol: str, interval: str = "1h") -> dict:
        """
        Return real-time market data for *symbol*.

        Includes: mark price, last price, funding rate, open interest,
        volume, and OHLCV klines.
        """
        try:
            mark_data = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
            ticker = self._get("/fapi/v1/ticker/24hr", {"symbol": symbol})
            oi = self._get("/fapi/v1/openInterest", {"symbol": symbol})
            klines = self._get(
                "/fapi/v1/klines",
                {"symbol": symbol, "interval": interval, "limit": 100},
            )

            result = {
                "symbol": symbol,
                "timestamp": _ts(),
                "mark_price": float(mark_data.get("markPrice", 0)),
                "last_price": float(ticker.get("lastPrice", 0)),
                "funding_rate": float(mark_data.get("lastFundingRate", 0)),
                "next_funding_time": mark_data.get("nextFundingTime"),
                "open_interest": float(oi.get("openInterest", 0)),
                "volume_24h": float(ticker.get("volume", 0)),
                "quote_volume_24h": float(ticker.get("quoteVolume", 0)),
                "price_change_pct": float(ticker.get("priceChangePercent", 0)),
                "klines": klines,
            }
            return result
        except Exception as exc:
            trade_logger.log_error(f"get_futures_market_data({symbol})", exc)
            return {"symbol": symbol, "error": str(exc)}

    def fetch_market_news(self, query: str = "cryptocurrency bitcoin", page_size: int = 5) -> list[dict]:
        """
        Retrieve the latest cryptocurrency news via NewsAPI.

        Returns a list of article dicts with keys: title, source, url, publishedAt.
        """
        if not Config.NEWS_API_KEY:
            logger.warning("NEWS_API_KEY not configured – skipping news fetch")
            return []
        try:
            params = {
                "q": query,
                "apiKey": Config.NEWS_API_KEY,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": page_size,
            }
            resp = requests.get(Config.NEWS_API_URL, params=params, timeout=10)
            resp.raise_for_status()
            articles = resp.json().get("articles", [])
            return [
                {
                    "title": a.get("title"),
                    "source": a.get("source", {}).get("name"),
                    "url": a.get("url"),
                    "publishedAt": a.get("publishedAt"),
                }
                for a in articles
            ]
        except Exception as exc:
            trade_logger.log_error("fetch_market_news", exc)
            return []

    # ════════════════════════════════════════════════════════════════════════
    # 2.  TECHNICAL ANALYSIS
    # ════════════════════════════════════════════════════════════════════════

    @staticmethod
    def calculate_indicators_futures(data: list) -> dict:
        """
        Calculate technical indicators from raw kline data.

        Parameters
        ----------
        data:
            Raw klines list as returned by ``/fapi/v1/klines``
            (each item: [open_time, open, high, low, close, volume, …]).

        Returns
        -------
        dict
            Keys: ma_20, ma_50, rsi, macd, macd_signal, macd_hist,
            bb_upper, bb_middle, bb_lower, volume_profile, support, resistance.
        """
        if not data or len(data) < 2:
            return {}

        df = pd.DataFrame(
            data,
            columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "taker_buy_base", "taker_buy_quote", "ignore",
            ],
        )
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["volume"] = df["volume"].astype(float)

        close = df["close"]

        # Moving averages
        ma_20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else float(close.mean())
        ma_50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else float(close.mean())

        # RSI (14)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi_series = 100 - (100 / (1 + rs))
        rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0

        # MACD (12, 26, 9)
        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line

        # Bollinger Bands (20, 2σ)
        bb_middle = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_upper = bb_middle + 2 * bb_std
        bb_lower = bb_middle - 2 * bb_std

        # Volume Profile (simple: mean volume of recent 20 bars)
        volume_profile = float(df["volume"].rolling(20).mean().iloc[-1]) if len(df) >= 20 else float(df["volume"].mean())

        # Basic support / resistance (min/max of last 20 bars)
        support = float(df["low"].tail(20).min())
        resistance = float(df["high"].tail(20).max())

        return {
            "ma_20": round(ma_20, 4),
            "ma_50": round(ma_50, 4),
            "rsi": round(rsi, 2),
            "macd": round(float(macd_line.iloc[-1]), 6),
            "macd_signal": round(float(signal_line.iloc[-1]), 6),
            "macd_hist": round(float(macd_hist.iloc[-1]), 6),
            "bb_upper": round(float(bb_upper.iloc[-1]), 4),
            "bb_middle": round(float(bb_middle.iloc[-1]), 4),
            "bb_lower": round(float(bb_lower.iloc[-1]), 4),
            "volume_profile": round(volume_profile, 4),
            "support": round(support, 4),
            "resistance": round(resistance, 4),
            "current_price": round(float(close.iloc[-1]), 4),
        }

    @staticmethod
    def generate_futures_signals(indicators: dict, symbol: str = "") -> dict:
        """
        Generate a LONG/SHORT trading signal from pre-calculated indicators.

        Returns
        -------
        dict
            Keys: signal (LONG/SHORT/NEUTRAL), strength (0-100),
            recommended_leverage, reasons.
        """
        if not indicators:
            return {"signal": "NEUTRAL", "strength": 0, "recommended_leverage": 1, "reasons": []}

        reasons: list[str] = []
        long_score = 0
        short_score = 0

        rsi = indicators.get("rsi", 50)
        macd = indicators.get("macd", 0)
        macd_signal = indicators.get("macd_signal", 0)
        current_price = indicators.get("current_price", 0)
        ma_20 = indicators.get("ma_20", current_price)
        ma_50 = indicators.get("ma_50", current_price)
        bb_upper = indicators.get("bb_upper", current_price * 1.02)
        bb_lower = indicators.get("bb_lower", current_price * 0.98)

        # RSI
        if rsi < 30:
            long_score += 30
            reasons.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > 70:
            short_score += 30
            reasons.append(f"RSI overbought ({rsi:.1f})")

        # MACD crossover
        if macd > macd_signal:
            long_score += 25
            reasons.append("MACD bullish crossover")
        elif macd < macd_signal:
            short_score += 25
            reasons.append("MACD bearish crossover")

        # Moving average trend
        if ma_20 > ma_50:
            long_score += 20
            reasons.append("MA20 above MA50 (uptrend)")
        elif ma_20 < ma_50:
            short_score += 20
            reasons.append("MA20 below MA50 (downtrend)")

        # Bollinger Band position
        if current_price < bb_lower:
            long_score += 25
            reasons.append("Price below lower Bollinger Band")
        elif current_price > bb_upper:
            short_score += 25
            reasons.append("Price above upper Bollinger Band")

        total = long_score + short_score or 1
        if long_score > short_score:
            signal = "LONG"
            strength = round(long_score / total * 100)
        elif short_score > long_score:
            signal = "SHORT"
            strength = round(short_score / total * 100)
        else:
            signal = "NEUTRAL"
            strength = 0

        # Recommend leverage based on signal strength
        if strength >= 75:
            recommended_leverage = min(10, Config.MAX_LEVERAGE)
        elif strength >= 50:
            recommended_leverage = min(5, Config.MAX_LEVERAGE)
        else:
            recommended_leverage = min(3, Config.MAX_LEVERAGE)

        return {
            "symbol": symbol,
            "signal": signal,
            "strength": strength,
            "recommended_leverage": recommended_leverage,
            "reasons": reasons,
            "timestamp": _ts(),
        }

    # ════════════════════════════════════════════════════════════════════════
    # 3.  LEVERAGE & RISK MANAGEMENT
    # ════════════════════════════════════════════════════════════════════════

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """
        Set leverage for *symbol* (1–125x, capped by ``Config.MAX_LEVERAGE``).

        Returns
        -------
        dict with keys: status, symbol, leverage, message.
        """
        leverage = max(Config.MIN_LEVERAGE, min(leverage, Config.ABSOLUTE_MAX_LEVERAGE, Config.MAX_LEVERAGE))
        try:
            result = self._post(
                "/fapi/v1/leverage",
                {"symbol": symbol, "leverage": leverage},
            )
            self._leverages[symbol] = leverage
            logger.info("Leverage set | %s → %dx", symbol, leverage)
            self.send_futures_alert(f"Leverage set: {symbol} = {leverage}x", "LEVERAGE")
            return {"status": "success", "symbol": symbol, "leverage": leverage, "response": result}
        except Exception as exc:
            # Fallback: store locally even if API call fails (testnet/mock)
            self._leverages[symbol] = leverage
            trade_logger.log_error(f"set_leverage({symbol}, {leverage})", exc)
            return {"status": "error", "symbol": symbol, "leverage": leverage, "error": str(exc)}

    @staticmethod
    def get_liquidation_price(
        symbol: str,
        entry_price: float,
        quantity: float,
        leverage: int,
        side: str,
    ) -> dict:
        """
        Estimate the liquidation price for a position.

        Uses a simplified isolated-margin formula:
        - LONG:  liq_price ≈ entry_price × (1 − 1/leverage + maintenance_margin)
        - SHORT: liq_price ≈ entry_price × (1 + 1/leverage − maintenance_margin)

        The maintenance margin rate is approximated at 0.5 %.
        """
        maintenance_margin_rate = 0.005
        if side.upper() == "LONG":
            liq_price = entry_price * (1 - 1 / leverage + maintenance_margin_rate)
        else:
            liq_price = entry_price * (1 + 1 / leverage - maintenance_margin_rate)

        distance_pct = abs(_pct(liq_price, entry_price))
        return {
            "symbol": symbol,
            "side": side.upper(),
            "entry_price": entry_price,
            "liquidation_price": round(liq_price, 4),
            "distance_pct": distance_pct,
            "leverage": leverage,
            "quantity": quantity,
        }

    def check_margin_level(self, symbol: str) -> dict:
        """
        Retrieve margin / collateral info for *symbol*.

        Returns available balance, used margin, and a risk label.
        """
        try:
            account = self._get("/fapi/v2/account")
            position = next(
                (p for p in account.get("positions", []) if p["symbol"] == symbol),
                None,
            )
            available_balance = float(account.get("availableBalance", 0))
            total_margin = float(account.get("totalMarginBalance", 0))
            used_margin = total_margin - available_balance

            if total_margin > 0:
                utilisation = round(used_margin / total_margin * 100, 2)
            else:
                utilisation = 0.0

            risk = "LOW" if utilisation < 50 else ("MEDIUM" if utilisation < 75 else "HIGH")

            return {
                "symbol": symbol,
                "available_balance": available_balance,
                "total_margin": total_margin,
                "used_margin": round(used_margin, 4),
                "utilisation_pct": utilisation,
                "risk_level": risk,
                "position": position,
            }
        except Exception as exc:
            trade_logger.log_error(f"check_margin_level({symbol})", exc)
            return {"symbol": symbol, "error": str(exc)}

    def manage_drawdown_futures(self, current_balance: float) -> dict:
        """
        Enforce the maximum drawdown limit.

        If the drawdown exceeds ``Config.MAX_DRAWDOWN`` percent the bot will
        close all open positions and halt auto-trading.

        Returns
        -------
        dict with keys: status, drawdown_pct, action, message.
        """
        if self._daily_start_balance <= 0:
            self._daily_start_balance = current_balance
            return {"status": "ok", "drawdown_pct": 0.0, "action": "none", "message": "Baseline set"}

        drawdown_pct = _pct(current_balance, self._daily_start_balance)

        if drawdown_pct < 0 and abs(drawdown_pct) >= Config.MAX_DRAWDOWN:
            logger.warning(
                "Max drawdown breached! %.2f%% (limit: %.2f%%)",
                drawdown_pct,
                -Config.MAX_DRAWDOWN,
            )
            self.send_futures_alert(
                f"⚠️ Max drawdown breached: {drawdown_pct:.2f}% – closing all positions",
                "DRAWDOWN",
            )
            # Close all open positions
            for sym in list(self._positions.keys()):
                self.close_position(sym)
            return {
                "status": "breached",
                "drawdown_pct": drawdown_pct,
                "action": "closed_all",
                "message": f"Max drawdown {Config.MAX_DRAWDOWN}% exceeded – all positions closed",
            }

        return {
            "status": "ok",
            "drawdown_pct": drawdown_pct,
            "action": "none",
            "message": f"Drawdown within limits: {drawdown_pct:.2f}%",
        }

    # ════════════════════════════════════════════════════════════════════════
    # 4.  TRADING EXECUTION
    # ════════════════════════════════════════════════════════════════════════

    def execute_futures_trade(
        self,
        symbol: str,
        side: str,
        leverage: int,
        amount: float,
        stop_loss: float = Config.DEFAULT_STOP_LOSS,
        take_profit: float = Config.DEFAULT_TAKE_PROFIT,
    ) -> dict:
        """
        Open a LONG or SHORT futures position.

        Parameters
        ----------
        symbol:
            E.g. ``'BTCUSDT'``
        side:
            ``'LONG'`` or ``'SHORT'``
        leverage:
            Desired leverage (capped at ``Config.MAX_LEVERAGE``).
        amount:
            Notional size in USDT.
        stop_loss:
            Fraction of entry price to use as stop-loss distance (default 2 %).
        take_profit:
            Fraction of entry price to use as take-profit distance (default 4 %).
        """
        side_upper = side.upper()
        if side_upper not in ("LONG", "SHORT"):
            return {"status": "error", "message": "side must be LONG or SHORT"}

        # Set leverage
        self.set_leverage(symbol, leverage)

        binance_side = "BUY" if side_upper == "LONG" else "SELL"

        try:
            # Fetch current mark price to compute quantity
            mark_resp = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
            entry_price = float(mark_resp.get("markPrice", 0))
            if entry_price == 0:
                return {"status": "error", "message": "Could not fetch mark price"}

            quantity = round(amount / entry_price, 3)

            # Place market order
            order = self._post(
                "/fapi/v1/order",
                {
                    "symbol": symbol,
                    "side": binance_side,
                    "type": "MARKET",
                    "quantity": quantity,
                },
            )
            order_id = str(order.get("orderId", ""))
            fill_price = float(order.get("avgPrice", entry_price))

            # Compute SL / TP prices
            if side_upper == "LONG":
                sl_price = round(fill_price * (1 - stop_loss), 4)
                tp_price = round(fill_price * (1 + take_profit), 4)
            else:
                sl_price = round(fill_price * (1 + stop_loss), 4)
                tp_price = round(fill_price * (1 - take_profit), 4)

            position = {
                "symbol": symbol,
                "side": side_upper,
                "leverage": leverage,
                "entry_price": fill_price,
                "quantity": quantity,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "order_id": order_id,
                "opened_at": _ts(),
                "amount_usdt": amount,
            }
            self._positions[symbol] = position
            self._trade_history.append({**position, "event": "OPEN"})

            trade_logger.log_trade_open(
                symbol, side_upper, leverage, fill_price, quantity, sl_price, tp_price, order_id
            )
            self.send_position_alert(symbol, "opened", fill_price, sl_price, tp_price)

            # Liquidation check
            liq = self.get_liquidation_price(symbol, fill_price, quantity, leverage, side_upper)
            if liq["distance_pct"] < 5:
                self.send_liquidation_alert(symbol, liq["liquidation_price"])

            return {"status": "success", "position": position, "order": order}

        except Exception as exc:
            trade_logger.log_error(f"execute_futures_trade({symbol})", exc)
            return {"status": "error", "symbol": symbol, "error": str(exc)}

    def close_position(self, symbol: str) -> dict:
        """
        Close the open position for *symbol* and calculate P&L.

        Returns
        -------
        dict with keys: status, symbol, pnl, entry_price, exit_price.
        """
        position = self._positions.get(symbol)
        if not position:
            return {"status": "error", "message": f"No open position for {symbol}"}

        # Opposite side to close
        close_side = "SELL" if position["side"] == "LONG" else "BUY"

        try:
            mark_resp = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
            exit_price = float(mark_resp.get("markPrice", position["entry_price"]))

            order = self._post(
                "/fapi/v1/order",
                {
                    "symbol": symbol,
                    "side": close_side,
                    "type": "MARKET",
                    "quantity": position["quantity"],
                    "reduceOnly": "true",
                },
            )

            # P&L calculation
            if position["side"] == "LONG":
                pnl = (exit_price - position["entry_price"]) * position["quantity"]
            else:
                pnl = (position["entry_price"] - exit_price) * position["quantity"]
            pnl = round(pnl, 4)

            trade_logger.log_trade_close(
                symbol, position["side"], position["entry_price"], exit_price, position["quantity"], pnl
            )
            self._trade_history.append({
                "event": "CLOSE",
                "symbol": symbol,
                "exit_price": exit_price,
                "pnl": pnl,
                "timestamp": _ts(),
            })
            del self._positions[symbol]
            self.send_position_alert(symbol, "closed", exit_price, pnl=pnl)

            return {
                "status": "success",
                "symbol": symbol,
                "entry_price": position["entry_price"],
                "exit_price": exit_price,
                "quantity": position["quantity"],
                "pnl": pnl,
                "order": order,
            }

        except Exception as exc:
            trade_logger.log_error(f"close_position({symbol})", exc)
            return {"status": "error", "symbol": symbol, "error": str(exc)}

    def update_position(self, symbol: str, new_sl: Optional[float] = None, new_tp: Optional[float] = None) -> dict:
        """
        Update the stop-loss and/or take-profit for an open position.

        Returns
        -------
        dict with keys: status, symbol, new_stop_loss, new_take_profit.
        """
        position = self._positions.get(symbol)
        if not position:
            return {"status": "error", "message": f"No open position for {symbol}"}

        if new_sl is not None:
            position["stop_loss"] = new_sl
        if new_tp is not None:
            position["take_profit"] = new_tp

        trade_logger.log_position_update(symbol, new_sl, new_tp)
        return {
            "status": "success",
            "symbol": symbol,
            "new_stop_loss": position.get("stop_loss"),
            "new_take_profit": position.get("take_profit"),
        }

    def get_open_positions(self) -> list[dict]:
        """
        Return a list of all currently open positions with unrealised P&L.

        Each item contains: symbol, side, entry_price, current_price, pnl,
        leverage, quantity, stop_loss, take_profit.
        """
        result = []
        for symbol, pos in list(self._positions.items()):
            try:
                mark_resp = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
                current_price = float(mark_resp.get("markPrice", pos["entry_price"]))
            except Exception:
                current_price = pos["entry_price"]

            if pos["side"] == "LONG":
                upnl = (current_price - pos["entry_price"]) * pos["quantity"]
            else:
                upnl = (pos["entry_price"] - current_price) * pos["quantity"]

            liq = self.get_liquidation_price(symbol, pos["entry_price"], pos["quantity"], pos["leverage"], pos["side"])

            result.append({
                **pos,
                "current_price": current_price,
                "unrealised_pnl": round(upnl, 4),
                "pnl_pct": _pct(current_price, pos["entry_price"]) * pos["leverage"],
                "liquidation_price": liq["liquidation_price"],
            })
        return result

    # ════════════════════════════════════════════════════════════════════════
    # 5.  ADVANCED TRADING FEATURES
    # ════════════════════════════════════════════════════════════════════════

    def execute_grid_trade(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        grid_levels: int = Config.DEFAULT_GRID_LEVELS,
        grid_spacing: float = Config.DEFAULT_GRID_SPACING,
    ) -> dict:
        """
        Set up a grid of limit orders around *entry_price*.

        Each level is spaced ``grid_spacing`` (fraction) apart.
        """
        if grid_levels < 2:
            return {"status": "error", "message": "grid_levels must be at least 2"}

        levels = []
        for i in range(grid_levels):
            offset = (i + 1) * grid_spacing
            if side.upper() == "LONG":
                price = round(entry_price * (1 - offset), 4)
                tp = round(price * (1 + grid_spacing * 2), 4)
                sl = round(price * (1 - grid_spacing), 4)
            else:
                price = round(entry_price * (1 + offset), 4)
                tp = round(price * (1 - grid_spacing * 2), 4)
                sl = round(price * (1 + grid_spacing), 4)

            levels.append({
                "level": i + 1,
                "price": price,
                "take_profit": tp,
                "stop_loss": sl,
                "status": "pending",
            })

        self._grid_positions[symbol] = levels
        logger.info("Grid trade set up | %s | %d levels | entry=%.4f", symbol, grid_levels, entry_price)
        return {
            "status": "success",
            "symbol": symbol,
            "side": side.upper(),
            "entry_price": entry_price,
            "grid_levels": grid_levels,
            "grid_spacing": grid_spacing,
            "levels": levels,
        }

    def scale_winning_position(self, symbol: str, scale_factor: float = 0.5) -> dict:
        """
        Add capital to a currently winning position by a *scale_factor* fraction.

        E.g. ``scale_factor=0.5`` adds 50 % of the original notional size.
        """
        position = self._positions.get(symbol)
        if not position:
            return {"status": "error", "message": f"No open position for {symbol}"}

        try:
            mark_resp = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
            current_price = float(mark_resp.get("markPrice", position["entry_price"]))
        except Exception:
            current_price = position["entry_price"]

        # Only scale if in profit
        is_winning = (
            (position["side"] == "LONG" and current_price > position["entry_price"])
            or (position["side"] == "SHORT" and current_price < position["entry_price"])
        )
        if not is_winning:
            return {"status": "skipped", "message": f"{symbol} is not in profit – skipping scale"}

        add_quantity = round(position["quantity"] * scale_factor, 3)
        binance_side = "BUY" if position["side"] == "LONG" else "SELL"

        try:
            order = self._post(
                "/fapi/v1/order",
                {"symbol": symbol, "side": binance_side, "type": "MARKET", "quantity": add_quantity},
            )
            position["quantity"] = round(position["quantity"] + add_quantity, 3)
            logger.info("Scaled position | %s | +%.3f @ %.4f", symbol, add_quantity, current_price)
            return {
                "status": "success",
                "symbol": symbol,
                "added_quantity": add_quantity,
                "new_total_quantity": position["quantity"],
                "current_price": current_price,
                "order": order,
            }
        except Exception as exc:
            trade_logger.log_error(f"scale_winning_position({symbol})", exc)
            return {"status": "error", "symbol": symbol, "error": str(exc)}

    def auto_reentry(self, symbol: str, retry_count: int = Config.MAX_REENTRY_RETRIES, amount: float = Config.DEFAULT_TRADE_AMOUNT) -> dict:
        """
        Automatically re-enter a trade that was stopped out.

        Waits 60 seconds between retries and checks signal strength before
        each attempt. Returns after exhausting *retry_count* attempts.
        """
        for attempt in range(1, retry_count + 1):
            logger.info("Auto-reentry | %s | attempt %d/%d", symbol, attempt, retry_count)
            try:
                raw_data = self.get_futures_market_data(symbol)
                klines = raw_data.get("klines", [])
                if not klines:
                    time.sleep(60)
                    continue

                indicators = self.calculate_indicators_futures(klines)
                signal_info = self.generate_futures_signals(indicators, symbol)

                if signal_info["signal"] in ("LONG", "SHORT") and signal_info["strength"] >= 60:
                    result = self.execute_futures_trade(
                        symbol=symbol,
                        side=signal_info["signal"],
                        leverage=signal_info["recommended_leverage"],
                        amount=amount,
                    )
                    if result.get("status") == "success":
                        return {"status": "success", "attempt": attempt, "trade": result}

            except Exception as exc:
                trade_logger.log_error(f"auto_reentry({symbol}) attempt {attempt}", exc)

            if attempt < retry_count:
                time.sleep(60)

        return {"status": "failed", "message": f"Auto-reentry failed after {retry_count} attempts", "symbol": symbol}

    # ════════════════════════════════════════════════════════════════════════
    # 6.  PORTFOLIO MANAGEMENT
    # ════════════════════════════════════════════════════════════════════════

    def allocate_futures_capital(self, total_amount: float, leverage: int = Config.DEFAULT_LEVERAGE) -> dict:
        """
        Distribute *total_amount* USDT evenly across the top N USDT pairs.

        Opens a position on each symbol based on its latest signal.
        """
        symbols = self.fetch_futures_symbols()[: Config.TOP_N_SYMBOLS]
        if not symbols:
            return {"status": "error", "message": "No symbols fetched"}

        amount_per_symbol = total_amount / len(symbols)
        results = []

        for symbol in symbols:
            try:
                raw_data = self.get_futures_market_data(symbol)
                klines = raw_data.get("klines", [])
                indicators = self.calculate_indicators_futures(klines)
                signal_info = self.generate_futures_signals(indicators, symbol)

                if signal_info["signal"] in ("LONG", "SHORT"):
                    trade_result = self.execute_futures_trade(
                        symbol=symbol,
                        side=signal_info["signal"],
                        leverage=leverage,
                        amount=amount_per_symbol,
                    )
                    results.append({"symbol": symbol, "result": trade_result, "signal": signal_info["signal"]})
            except Exception as exc:
                results.append({"symbol": symbol, "result": {"status": "error", "error": str(exc)}})

        return {
            "status": "success",
            "total_amount": total_amount,
            "leverage": leverage,
            "symbols_count": len(symbols),
            "amount_per_symbol": amount_per_symbol,
            "trades": results,
        }

    def diversify_portfolio(self) -> dict:
        """
        Rebalance open positions: close positions with weak signals and
        redistribute capital to higher-conviction opportunities.
        """
        if not self._positions:
            return {"status": "ok", "message": "No open positions to rebalance"}

        closed = []
        for symbol in list(self._positions.keys()):
            try:
                raw_data = self.get_futures_market_data(symbol)
                klines = raw_data.get("klines", [])
                indicators = self.calculate_indicators_futures(klines)
                signal_info = self.generate_futures_signals(indicators, symbol)

                pos_side = self._positions[symbol]["side"]
                # Close if signal is opposite or weak
                if (
                    signal_info["signal"] != pos_side
                    or signal_info["strength"] < 40
                ):
                    result = self.close_position(symbol)
                    closed.append({"symbol": symbol, "close_result": result})
            except Exception as exc:
                trade_logger.log_error(f"diversify_portfolio({symbol})", exc)

        return {"status": "success", "rebalanced": len(closed), "closed": closed}

    # ════════════════════════════════════════════════════════════════════════
    # 7.  BACKTESTING & OPTIMISATION
    # ════════════════════════════════════════════════════════════════════════

    def backtest_futures_strategy(
        self,
        symbol: str,
        historical_data: list,
        leverage: int = Config.DEFAULT_LEVERAGE,
    ) -> dict:
        """
        Backtest the signal-based strategy on *historical_data*.

        Parameters
        ----------
        historical_data:
            List of klines (same format as ``/fapi/v1/klines``).
        leverage:
            Leverage to apply to all simulated trades.

        Returns
        -------
        dict
            Keys: roi, win_rate, total_trades, max_drawdown, profit_factor,
            trade_log.
        """
        if not historical_data or len(historical_data) < 50:
            return {"status": "error", "message": "Not enough historical data (need ≥50 bars)"}

        capital = Config.BACKTEST_INITIAL_CAPITAL
        peak_capital = capital
        max_drawdown = 0.0
        trade_log = []
        wins, losses = 0, 0
        gross_profit, gross_loss = 0.0, 0.0

        for i in range(50, len(historical_data) - 1):
            window = historical_data[: i + 1]
            indicators = self.calculate_indicators_futures(window)
            signal_info = self.generate_futures_signals(indicators, symbol)

            if signal_info["signal"] == "NEUTRAL" or signal_info["strength"] < 50:
                continue

            entry_price = float(historical_data[i][4])   # close price
            exit_price = float(historical_data[i + 1][4])  # next bar close

            if signal_info["signal"] == "LONG":
                pnl_pct = (exit_price - entry_price) / entry_price * leverage
            else:
                pnl_pct = (entry_price - exit_price) / entry_price * leverage

            trade_size = capital * (Config.RISK_PER_TRADE / 100)
            pnl = trade_size * pnl_pct
            capital += pnl

            if pnl > 0:
                wins += 1
                gross_profit += pnl
            else:
                losses += 1
                gross_loss += abs(pnl)

            if capital > peak_capital:
                peak_capital = capital
            dd = (peak_capital - capital) / peak_capital * 100
            if dd > max_drawdown:
                max_drawdown = dd

            trade_log.append({
                "bar": i,
                "signal": signal_info["signal"],
                "entry": entry_price,
                "exit": exit_price,
                "pnl": round(pnl, 4),
                "capital": round(capital, 2),
            })

        total_trades = wins + losses
        roi = _pct(capital, Config.BACKTEST_INITIAL_CAPITAL)
        win_rate = round(wins / total_trades * 100, 2) if total_trades else 0.0
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else float("inf")

        return {
            "status": "success",
            "symbol": symbol,
            "leverage": leverage,
            "initial_capital": Config.BACKTEST_INITIAL_CAPITAL,
            "final_capital": round(capital, 2),
            "roi": roi,
            "win_rate": win_rate,
            "total_trades": total_trades,
            "winning_trades": wins,
            "losing_trades": losses,
            "max_drawdown": round(max_drawdown, 2),
            "profit_factor": profit_factor,
            "trade_log": trade_log,
        }

    def optimize_parameters(self, symbol: str, historical_data: list) -> dict:
        """
        Grid-search over leverage values to find the best back-tested ROI.

        Returns the leverage setting that produced the highest ROI.
        """
        if not historical_data:
            return {"status": "error", "message": "No historical data provided"}

        best = {"leverage": 1, "roi": float("-inf")}
        results = []

        for lev in [1, 2, 3, 5, 7, 10]:
            bt = self.backtest_futures_strategy(symbol, historical_data, leverage=lev)
            if bt.get("status") == "success":
                roi = bt["roi"]
                results.append({"leverage": lev, "roi": roi, "win_rate": bt["win_rate"]})
                if roi > best["roi"]:
                    best = {"leverage": lev, "roi": roi}

        return {
            "status": "success",
            "symbol": symbol,
            "best_leverage": best["leverage"],
            "best_roi": best["roi"],
            "all_results": results,
        }

    # ════════════════════════════════════════════════════════════════════════
    # 8.  PERFORMANCE TRACKING
    # ════════════════════════════════════════════════════════════════════════

    def track_futures_performance(self) -> dict:
        """
        Return a real-time performance summary from the in-memory trade history.

        Returns
        -------
        dict
            Keys: total_trades, winning_trades, losing_trades, win_rate,
            total_pnl, roi, max_drawdown, average_leverage, liquidation_count.
        """
        closed_trades = [t for t in self._trade_history if t.get("event") == "CLOSE"]
        if not closed_trades:
            return {
                "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
                "win_rate": 0.0, "total_pnl": 0.0, "roi": 0.0,
                "max_drawdown": 0.0, "average_leverage": 0.0, "liquidation_count": 0,
            }

        pnls = [t.get("pnl", 0) for t in closed_trades]
        winning = [p for p in pnls if p > 0]
        total_pnl = sum(pnls)

        # Max drawdown from cumulative PnL curve
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        open_events = [t for t in self._trade_history if t.get("event") == "OPEN"]
        leverages = [t.get("leverage", 1) for t in open_events]
        avg_leverage = round(sum(leverages) / len(leverages), 2) if leverages else 0.0

        roi = round(total_pnl / Config.BACKTEST_INITIAL_CAPITAL * 100, 2)

        return {
            "total_trades": len(closed_trades),
            "winning_trades": len(winning),
            "losing_trades": len(closed_trades) - len(winning),
            "win_rate": round(len(winning) / len(closed_trades) * 100, 2) if closed_trades else 0.0,
            "total_pnl": round(total_pnl, 4),
            "roi": roi,
            "max_drawdown": round(max_dd, 4),
            "average_leverage": avg_leverage,
            "liquidation_count": 0,
            "open_positions": len(self._positions),
        }

    def generate_daily_report(self) -> dict:
        """Generate a daily performance summary."""
        today = datetime.now(tz=timezone.utc).date().isoformat()
        perf = self.track_futures_performance()
        report = {
            "date": today,
            "report_type": "DAILY",
            **perf,
            "open_positions": self.get_open_positions(),
        }
        trade_logger.log_performance(report)
        self.send_futures_alert(
            f"📊 Daily Report ({today}): PnL={perf['total_pnl']:.2f} | "
            f"Win Rate={perf['win_rate']}% | Trades={perf['total_trades']}",
            "REPORT",
        )
        return report

    def generate_monthly_report(self) -> dict:
        """Generate a monthly analytics summary."""
        now = datetime.now(tz=timezone.utc)
        month_label = now.strftime("%Y-%m")
        perf = self.track_futures_performance()
        report = {
            "month": month_label,
            "report_type": "MONTHLY",
            **perf,
        }
        trade_logger.log_performance(report)
        self.send_futures_alert(
            f"📈 Monthly Report ({month_label}): ROI={perf['roi']}% | "
            f"Total PnL={perf['total_pnl']:.2f} | Trades={perf['total_trades']}",
            "REPORT",
        )
        return report

    # ════════════════════════════════════════════════════════════════════════
    # 9.  ALERTS
    # ════════════════════════════════════════════════════════════════════════

    def send_futures_alert(self, message: str, alert_type: str = "INFO") -> bool:
        """
        Send a Telegram notification.

        Returns *True* on success, *False* otherwise.
        """
        trade_logger.log_alert(message, alert_type)
        if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
            return False
        try:
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": Config.TELEGRAM_CHAT_ID,
                "text": f"[{alert_type}] {message}",
                "parse_mode": "HTML",
            }
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as exc:
            trade_logger.log_error("send_futures_alert", exc)
            return False

    def send_position_alert(
        self,
        symbol: str,
        action: str,
        price: float = 0.0,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        pnl: Optional[float] = None,
    ) -> bool:
        """Send a trade opened / closed alert."""
        if action == "opened":
            msg = (
                f"🟢 Position OPENED: {symbol}\n"
                f"Price: {price}\nSL: {sl}\nTP: {tp}"
            )
        else:
            msg = f"🔴 Position CLOSED: {symbol}\nPrice: {price}\nP&L: {pnl}"
        return self.send_futures_alert(msg, "POSITION")

    def send_margin_warning(self, symbol: str, utilisation_pct: float) -> bool:
        """Send a low-margin warning alert."""
        msg = f"⚠️ Low Margin Warning: {symbol}\nMargin utilisation: {utilisation_pct:.1f}%"
        return self.send_futures_alert(msg, "MARGIN_WARNING")

    def send_liquidation_alert(self, symbol: str, liquidation_price: float) -> bool:
        """Send a liquidation risk alert."""
        msg = f"🚨 Liquidation Risk: {symbol}\nLiquidation price: {liquidation_price}"
        return self.send_futures_alert(msg, "LIQUIDATION")

    # ════════════════════════════════════════════════════════════════════════
    # 10.  MAIN LOOP
    # ════════════════════════════════════════════════════════════════════════

    def monitor_positions(self, interval_seconds: int = 60) -> None:
        """
        Continuously monitor open positions; enforce SL/TP and margin limits.

        This method runs indefinitely. Press Ctrl+C to stop.
        """
        logger.info("Starting position monitor (interval=%ds)", interval_seconds)
        while True:
            try:
                for symbol, pos in list(self._positions.items()):
                    try:
                        mark_resp = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
                        current_price = float(mark_resp.get("markPrice", pos["entry_price"]))
                    except Exception:
                        continue

                    sl = pos.get("stop_loss")
                    tp = pos.get("take_profit")

                    # Check stop-loss
                    if sl and (
                        (pos["side"] == "LONG" and current_price <= sl)
                        or (pos["side"] == "SHORT" and current_price >= sl)
                    ):
                        logger.warning("Stop-loss triggered | %s @ %.4f", symbol, current_price)
                        self.close_position(symbol)
                        continue

                    # Check take-profit
                    if tp and (
                        (pos["side"] == "LONG" and current_price >= tp)
                        or (pos["side"] == "SHORT" and current_price <= tp)
                    ):
                        logger.info("Take-profit hit | %s @ %.4f", symbol, current_price)
                        self.close_position(symbol)
                        continue

                    # Margin check
                    margin = self.check_margin_level(symbol)
                    if margin.get("utilisation_pct", 0) > 80:
                        self.send_margin_warning(symbol, margin["utilisation_pct"])

            except KeyboardInterrupt:
                logger.info("Position monitor stopped by user")
                break
            except Exception as exc:
                trade_logger.log_error("monitor_positions", exc)

            time.sleep(interval_seconds)

    def start_trading(self, symbols: Optional[list[str]] = None) -> None:
        """
        Main auto-trading loop.

        Fetches signals for each symbol every minute and executes trades when
        a strong signal is detected. Requires ``Config.AUTO_TRADING = True``.
        """
        if not Config.AUTO_TRADING:
            logger.info("AUTO_TRADING is disabled. Set AUTO_TRADING=true in .env to enable.")
            return

        if symbols is None:
            symbols = self.fetch_futures_symbols()[: Config.TOP_N_SYMBOLS]

        logger.info("Starting auto-trading loop | symbols=%s", symbols)

        while True:
            try:
                for symbol in symbols:
                    if symbol in self._positions:
                        continue  # Already in a trade for this symbol
                    if len(self._positions) >= Config.MAX_OPEN_POSITIONS:
                        break

                    raw_data = self.get_futures_market_data(symbol)
                    klines = raw_data.get("klines", [])
                    if not klines:
                        continue

                    indicators = self.calculate_indicators_futures(klines)
                    signal_info = self.generate_futures_signals(indicators, symbol)

                    if signal_info["signal"] != "NEUTRAL" and signal_info["strength"] >= 60:
                        self.execute_futures_trade(
                            symbol=symbol,
                            side=signal_info["signal"],
                            leverage=signal_info["recommended_leverage"],
                            amount=Config.DEFAULT_TRADE_AMOUNT,
                        )
            except KeyboardInterrupt:
                logger.info("Auto-trading stopped by user")
                break
            except Exception as exc:
                trade_logger.log_error("start_trading", exc)

            time.sleep(60)
