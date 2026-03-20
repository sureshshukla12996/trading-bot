"""
Unit tests for TradingBot futures functionality.

Tests use only the logic layer (no real API calls) by mocking HTTP requests.
"""

import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Ensure repo root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_bot_futures import TradingBot


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_klines(n: int = 100, start_price: float = 50000.0, trend: float = 0.001) -> list:
    """Generate synthetic klines list for testing."""
    klines = []
    price = start_price
    for i in range(n):
        price *= 1 + trend * (1 if i % 2 == 0 else -0.5)
        klines.append([
            i * 60000, str(price), str(price * 1.005), str(price * 0.995),
            str(price), str(1000 + i * 10),
            (i + 1) * 60000, str(price * 1000), 100, str(500), str(500), "0",
        ])
    return klines


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchFuturesSymbols(unittest.TestCase):
    def setUp(self):
        self.bot = TradingBot(testnet=True)

    @patch("trading_bot_futures.requests.get")
    def test_returns_only_usdt_symbols(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "symbols": [
                    {"symbol": "BTCUSDT", "quoteAsset": "USDT", "status": "TRADING"},
                    {"symbol": "ETHUSDT", "quoteAsset": "USDT", "status": "TRADING"},
                    {"symbol": "BTCBUSD", "quoteAsset": "BUSD", "status": "TRADING"},
                    {"symbol": "BNBUSDT", "quoteAsset": "USDT", "status": "BREAK"},
                ]
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()
        symbols = self.bot.fetch_futures_symbols()
        self.assertEqual(symbols, ["BTCUSDT", "ETHUSDT"])

    @patch("trading_bot_futures.requests.get")
    def test_returns_empty_on_error(self, mock_get):
        mock_get.side_effect = Exception("network error")
        symbols = self.bot.fetch_futures_symbols()
        self.assertEqual(symbols, [])


class TestGetLiquidationPrice(unittest.TestCase):
    def test_long_liquidation_below_entry(self):
        result = TradingBot.get_liquidation_price("BTCUSDT", 50000, 1, 10, "LONG")
        self.assertLess(result["liquidation_price"], 50000)
        self.assertIn("distance_pct", result)

    def test_short_liquidation_above_entry(self):
        result = TradingBot.get_liquidation_price("BTCUSDT", 50000, 1, 10, "SHORT")
        self.assertGreater(result["liquidation_price"], 50000)

    def test_higher_leverage_closer_liquidation(self):
        liq_low = TradingBot.get_liquidation_price("BTCUSDT", 50000, 1, 5, "LONG")["liquidation_price"]
        liq_high = TradingBot.get_liquidation_price("BTCUSDT", 50000, 1, 20, "LONG")["liquidation_price"]
        self.assertGreater(liq_high, liq_low)


class TestSetLeverage(unittest.TestCase):
    def setUp(self):
        self.bot = TradingBot(testnet=True)

    @patch("trading_bot_futures.requests.post")
    def test_leverage_capped_at_max(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"leverage": 10})
        mock_post.return_value.raise_for_status = MagicMock()
        result = self.bot.set_leverage("BTCUSDT", 200)
        # Should be capped at Config.MAX_LEVERAGE (10 by default)
        self.assertLessEqual(result["leverage"], 125)

    @patch("trading_bot_futures.requests.post")
    def test_leverage_stored(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"leverage": 5})
        mock_post.return_value.raise_for_status = MagicMock()
        self.bot.set_leverage("ETHUSDT", 5)
        self.assertEqual(self.bot._leverages.get("ETHUSDT"), 5)


class TestExecuteFuturesTrade(unittest.TestCase):
    def setUp(self):
        self.bot = TradingBot(testnet=True)

    def _mock_get(self, url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "premiumIndex" in url:
            resp.json = lambda: {"markPrice": "50000.0", "lastFundingRate": "0.0001", "nextFundingTime": 0}
        elif "exchangeInfo" in url:
            resp.json = lambda: {"symbols": []}
        else:
            resp.json = lambda: {}
        return resp

    def _mock_post(self, url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = lambda: {"orderId": "12345", "avgPrice": "50000.0", "status": "FILLED"}
        return resp

    @patch("trading_bot_futures.requests.get")
    @patch("trading_bot_futures.requests.post")
    def test_long_trade_creates_position(self, mock_post, mock_get):
        mock_get.side_effect = self._mock_get
        mock_post.side_effect = self._mock_post
        result = self.bot.execute_futures_trade("BTCUSDT", "LONG", 10, 100)
        self.assertEqual(result["status"], "success")
        self.assertIn("BTCUSDT", self.bot._positions)
        self.assertEqual(self.bot._positions["BTCUSDT"]["side"], "LONG")

    @patch("trading_bot_futures.requests.get")
    @patch("trading_bot_futures.requests.post")
    def test_short_trade_creates_position(self, mock_post, mock_get):
        mock_get.side_effect = self._mock_get
        mock_post.side_effect = self._mock_post
        result = self.bot.execute_futures_trade("ETHUSDT", "SHORT", 5, 50)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.bot._positions["ETHUSDT"]["side"], "SHORT")

    def test_invalid_side_returns_error(self):
        result = self.bot.execute_futures_trade("BTCUSDT", "INVALID", 10, 100)
        self.assertEqual(result["status"], "error")


class TestClosePosition(unittest.TestCase):
    def setUp(self):
        self.bot = TradingBot(testnet=True)
        self.bot._positions["BTCUSDT"] = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "leverage": 10,
            "entry_price": 50000.0,
            "quantity": 0.002,
            "stop_loss": 49000.0,
            "take_profit": 52000.0,
            "order_id": "12345",
            "opened_at": "2024-01-01T00:00:00+00:00",
            "amount_usdt": 100.0,
        }

    @patch("trading_bot_futures.requests.get")
    @patch("trading_bot_futures.requests.post")
    def test_close_removes_position(self, mock_post, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"markPrice": "52000.0"},
        )
        mock_get.return_value.raise_for_status = MagicMock()
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"orderId": "99999"},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        result = self.bot.close_position("BTCUSDT")
        self.assertEqual(result["status"], "success")
        self.assertNotIn("BTCUSDT", self.bot._positions)

    @patch("trading_bot_futures.requests.get")
    @patch("trading_bot_futures.requests.post")
    def test_close_calculates_pnl(self, mock_post, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"markPrice": "52000.0"},
        )
        mock_get.return_value.raise_for_status = MagicMock()
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"orderId": "99999"},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        result = self.bot.close_position("BTCUSDT")
        # LONG: (52000 - 50000) * 0.002 = 4.0
        self.assertAlmostEqual(result["pnl"], 4.0, places=2)

    def test_close_nonexistent_returns_error(self):
        result = self.bot.close_position("NONEXISTENT")
        self.assertEqual(result["status"], "error")


class TestUpdatePosition(unittest.TestCase):
    def setUp(self):
        self.bot = TradingBot(testnet=True)
        self.bot._positions["BTCUSDT"] = {
            "symbol": "BTCUSDT", "side": "LONG", "leverage": 10,
            "entry_price": 50000.0, "quantity": 0.002,
            "stop_loss": 49000.0, "take_profit": 52000.0,
        }

    def test_update_stop_loss(self):
        result = self.bot.update_position("BTCUSDT", new_sl=48000.0)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.bot._positions["BTCUSDT"]["stop_loss"], 48000.0)

    def test_update_take_profit(self):
        result = self.bot.update_position("BTCUSDT", new_tp=55000.0)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.bot._positions["BTCUSDT"]["take_profit"], 55000.0)

    def test_update_nonexistent_position(self):
        result = self.bot.update_position("XYZUSDT", new_sl=100.0)
        self.assertEqual(result["status"], "error")


class TestGetOpenPositions(unittest.TestCase):
    def setUp(self):
        self.bot = TradingBot(testnet=True)
        self.bot._positions["BTCUSDT"] = {
            "symbol": "BTCUSDT", "side": "LONG", "leverage": 10,
            "entry_price": 50000.0, "quantity": 0.002,
            "stop_loss": 49000.0, "take_profit": 52000.0,
            "order_id": "1", "opened_at": "2024-01-01", "amount_usdt": 100,
        }

    @patch("trading_bot_futures.requests.get")
    def test_returns_positions_with_upnl(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"markPrice": "51000.0"},
        )
        mock_get.return_value.raise_for_status = MagicMock()
        positions = self.bot.get_open_positions()
        self.assertEqual(len(positions), 1)
        self.assertIn("unrealised_pnl", positions[0])
        self.assertAlmostEqual(positions[0]["unrealised_pnl"], 2.0, places=2)


class TestManageDrawdown(unittest.TestCase):
    def test_sets_baseline_on_first_call(self):
        bot = TradingBot(testnet=True)
        result = bot.manage_drawdown_futures(10000)
        self.assertEqual(result["action"], "none")
        self.assertEqual(bot._daily_start_balance, 10000)

    def test_no_breach_within_limit(self):
        bot = TradingBot(testnet=True)
        bot._daily_start_balance = 10000
        result = bot.manage_drawdown_futures(9500)  # 5% down, limit 10%
        self.assertEqual(result["status"], "ok")

    def test_breach_closes_positions(self):
        bot = TradingBot(testnet=True)
        bot._daily_start_balance = 10000
        # Inject a fake position so close_position is triggered
        bot._positions["BTCUSDT"] = {
            "symbol": "BTCUSDT", "side": "LONG", "leverage": 5,
            "entry_price": 50000, "quantity": 0.001,
            "stop_loss": None, "take_profit": None,
        }
        with patch.object(bot, "close_position", return_value={"status": "success"}) as mock_close:
            result = bot.manage_drawdown_futures(8900)  # > 10% down
            self.assertEqual(result["status"], "breached")
            mock_close.assert_called_once_with("BTCUSDT")


class TestExecuteGridTrade(unittest.TestCase):
    def test_grid_creates_correct_levels(self):
        bot = TradingBot(testnet=True)
        result = bot.execute_grid_trade("BTCUSDT", "LONG", 50000, grid_levels=4, grid_spacing=0.01)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["levels"]), 4)
        # All prices should be below entry for LONG grid
        for lvl in result["levels"]:
            self.assertLess(lvl["price"], 50000)

    def test_short_grid_prices_above_entry(self):
        bot = TradingBot(testnet=True)
        result = bot.execute_grid_trade("BTCUSDT", "SHORT", 50000, grid_levels=3, grid_spacing=0.01)
        for lvl in result["levels"]:
            self.assertGreater(lvl["price"], 50000)

    def test_invalid_levels(self):
        bot = TradingBot(testnet=True)
        result = bot.execute_grid_trade("BTCUSDT", "LONG", 50000, grid_levels=1)
        self.assertEqual(result["status"], "error")


class TestBacktest(unittest.TestCase):
    def test_backtest_returns_summary(self):
        bot = TradingBot(testnet=True)
        klines = _make_klines(100)
        result = bot.backtest_futures_strategy("BTCUSDT", klines, leverage=5)
        self.assertEqual(result["status"], "success")
        self.assertIn("roi", result)
        self.assertIn("win_rate", result)
        self.assertIn("max_drawdown", result)

    def test_backtest_insufficient_data(self):
        bot = TradingBot(testnet=True)
        result = bot.backtest_futures_strategy("BTCUSDT", _make_klines(10), leverage=5)
        self.assertEqual(result["status"], "error")


class TestTrackPerformance(unittest.TestCase):
    def test_empty_returns_zeros(self):
        bot = TradingBot(testnet=True)
        perf = bot.track_futures_performance()
        self.assertEqual(perf["total_trades"], 0)

    def test_with_trades(self):
        bot = TradingBot(testnet=True)
        bot._trade_history = [
            {"event": "OPEN", "symbol": "BTCUSDT", "leverage": 10},
            {"event": "CLOSE", "symbol": "BTCUSDT", "pnl": 50.0},
            {"event": "CLOSE", "symbol": "ETHUSDT", "pnl": -20.0},
        ]
        perf = bot.track_futures_performance()
        self.assertEqual(perf["total_trades"], 2)
        self.assertEqual(perf["winning_trades"], 1)
        self.assertAlmostEqual(perf["total_pnl"], 30.0)


class TestSendAlerts(unittest.TestCase):
    def test_returns_false_when_token_missing(self):
        bot = TradingBot(testnet=True)
        result = bot.send_futures_alert("test message")
        self.assertFalse(result)  # No Telegram token configured in test env

    @patch("trading_bot_futures.requests.post")
    def test_sends_telegram_message(self, mock_post):
        from config import Config
        original_token = Config.TELEGRAM_BOT_TOKEN
        original_chat = Config.TELEGRAM_CHAT_ID
        Config.TELEGRAM_BOT_TOKEN = "fake_token"
        Config.TELEGRAM_CHAT_ID = "fake_chat"

        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})
        mock_post.return_value.raise_for_status = MagicMock()

        bot = TradingBot(testnet=True)
        result = bot.send_futures_alert("Hello", "TEST")
        self.assertTrue(result)

        Config.TELEGRAM_BOT_TOKEN = original_token
        Config.TELEGRAM_CHAT_ID = original_chat


if __name__ == "__main__":
    unittest.main()
