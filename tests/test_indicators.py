"""
Unit tests for the technical indicator calculations.

All tests run without any network access.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_bot_futures import TradingBot


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_klines(n: int = 100, start_price: float = 50000.0, trend: float = 0.001) -> list:
    """Return *n* synthetic klines with a slight upward trend."""
    klines = []
    price = start_price
    for i in range(n):
        price = price * (1 + trend * (1 if i % 3 != 0 else -0.5))
        high = price * 1.005
        low = price * 0.995
        volume = 1000.0 + i
        klines.append([
            i * 60000,       # open_time
            str(price),      # open
            str(high),       # high
            str(low),        # low
            str(price),      # close
            str(volume),     # volume
            (i + 1) * 60000, # close_time
            str(price * volume),  # quote_volume
            50,              # trades
            str(volume / 2), # taker_buy_base
            str(volume / 2), # taker_buy_quote
            "0",             # ignore
        ])
    return klines


def _make_downtrend_klines(n: int = 100, start_price: float = 50000.0) -> list:
    """Return *n* klines with a consistent downward trend."""
    return _make_klines(n, start_price, trend=-0.002)


# ─────────────────────────────────────────────────────────────────────────────
# Indicator tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateIndicators(unittest.TestCase):
    """Tests for calculate_indicators_futures."""

    def test_returns_empty_for_no_data(self):
        result = TradingBot.calculate_indicators_futures([])
        self.assertEqual(result, {})

    def test_returns_empty_for_single_bar(self):
        klines = _make_klines(1)
        result = TradingBot.calculate_indicators_futures(klines)
        self.assertEqual(result, {})

    def test_returns_all_keys(self):
        klines = _make_klines(100)
        result = TradingBot.calculate_indicators_futures(klines)
        expected_keys = {
            "ma_20", "ma_50", "rsi", "macd", "macd_signal", "macd_hist",
            "bb_upper", "bb_middle", "bb_lower", "volume_profile",
            "support", "resistance", "current_price",
        }
        self.assertTrue(expected_keys.issubset(result.keys()))

    def test_rsi_within_bounds(self):
        klines = _make_klines(100)
        result = TradingBot.calculate_indicators_futures(klines)
        self.assertGreaterEqual(result["rsi"], 0)
        self.assertLessEqual(result["rsi"], 100)

    def test_bollinger_bands_order(self):
        klines = _make_klines(100)
        result = TradingBot.calculate_indicators_futures(klines)
        self.assertGreater(result["bb_upper"], result["bb_middle"])
        self.assertGreater(result["bb_middle"], result["bb_lower"])

    def test_support_below_resistance(self):
        klines = _make_klines(100)
        result = TradingBot.calculate_indicators_futures(klines)
        self.assertLess(result["support"], result["resistance"])

    def test_ma20_and_ma50_are_floats(self):
        klines = _make_klines(100)
        result = TradingBot.calculate_indicators_futures(klines)
        self.assertIsInstance(result["ma_20"], float)
        self.assertIsInstance(result["ma_50"], float)

    def test_uptrend_ma20_above_ma50(self):
        """For a strong uptrend, MA20 should be above MA50."""
        klines = _make_klines(200, trend=0.005)
        result = TradingBot.calculate_indicators_futures(klines)
        self.assertGreater(result["ma_20"], result["ma_50"])

    def test_downtrend_ma20_below_ma50(self):
        """For a strong downtrend, MA20 should be below MA50."""
        klines = _make_downtrend_klines(200)
        result = TradingBot.calculate_indicators_futures(klines)
        self.assertLess(result["ma_20"], result["ma_50"])

    def test_volume_profile_positive(self):
        klines = _make_klines(100)
        result = TradingBot.calculate_indicators_futures(klines)
        self.assertGreater(result["volume_profile"], 0)

    def test_current_price_matches_last_close(self):
        klines = _make_klines(50)
        result = TradingBot.calculate_indicators_futures(klines)
        last_close = float(klines[-1][4])
        self.assertAlmostEqual(result["current_price"], last_close, places=2)


class TestGenerateFuturesSignals(unittest.TestCase):
    """Tests for generate_futures_signals."""

    def test_neutral_on_empty_indicators(self):
        result = TradingBot.generate_futures_signals({})
        self.assertEqual(result["signal"], "NEUTRAL")

    def test_signal_has_required_keys(self):
        klines = _make_klines(100)
        indicators = TradingBot.calculate_indicators_futures(klines)
        result = TradingBot.generate_futures_signals(indicators, "BTCUSDT")
        for key in ("signal", "strength", "recommended_leverage", "reasons"):
            self.assertIn(key, result)

    def test_signal_is_valid_value(self):
        klines = _make_klines(100)
        indicators = TradingBot.calculate_indicators_futures(klines)
        result = TradingBot.generate_futures_signals(indicators)
        self.assertIn(result["signal"], ("LONG", "SHORT", "NEUTRAL"))

    def test_strength_within_bounds(self):
        klines = _make_klines(100)
        indicators = TradingBot.calculate_indicators_futures(klines)
        result = TradingBot.generate_futures_signals(indicators)
        self.assertGreaterEqual(result["strength"], 0)
        self.assertLessEqual(result["strength"], 100)

    def test_recommended_leverage_positive(self):
        klines = _make_klines(100)
        indicators = TradingBot.calculate_indicators_futures(klines)
        result = TradingBot.generate_futures_signals(indicators)
        self.assertGreater(result["recommended_leverage"], 0)

    def test_oversold_rsi_generates_long(self):
        """Indicators with oversold RSI and bullish MACD should produce LONG."""
        indicators = {
            "rsi": 20,
            "macd": 0.5,
            "macd_signal": 0.1,
            "current_price": 100.0,
            "ma_20": 110.0,
            "ma_50": 100.0,
            "bb_upper": 120.0,
            "bb_lower": 80.0,
        }
        result = TradingBot.generate_futures_signals(indicators)
        self.assertEqual(result["signal"], "LONG")

    def test_overbought_rsi_generates_short(self):
        """Indicators with overbought RSI and bearish MACD should produce SHORT."""
        indicators = {
            "rsi": 80,
            "macd": -0.5,
            "macd_signal": 0.1,
            "current_price": 100.0,
            "ma_20": 90.0,
            "ma_50": 100.0,
            "bb_upper": 110.0,
            "bb_lower": 80.0,
        }
        result = TradingBot.generate_futures_signals(indicators)
        self.assertEqual(result["signal"], "SHORT")

    def test_reasons_list_populated(self):
        indicators = {
            "rsi": 20,
            "macd": 0.5,
            "macd_signal": 0.1,
            "current_price": 75.0,
            "ma_20": 110.0,
            "ma_50": 100.0,
            "bb_upper": 120.0,
            "bb_lower": 80.0,
        }
        result = TradingBot.generate_futures_signals(indicators)
        self.assertIsInstance(result["reasons"], list)
        self.assertGreater(len(result["reasons"]), 0)

    def test_symbol_included_in_result(self):
        klines = _make_klines(100)
        indicators = TradingBot.calculate_indicators_futures(klines)
        result = TradingBot.generate_futures_signals(indicators, "ETHUSDT")
        self.assertEqual(result.get("symbol"), "ETHUSDT")


class TestLiquidationPrice(unittest.TestCase):
    """Tests for get_liquidation_price."""

    def test_long_liq_below_entry(self):
        result = TradingBot.get_liquidation_price("BTCUSDT", 40000, 1, 10, "LONG")
        self.assertLess(result["liquidation_price"], 40000)

    def test_short_liq_above_entry(self):
        result = TradingBot.get_liquidation_price("BTCUSDT", 40000, 1, 10, "SHORT")
        self.assertGreater(result["liquidation_price"], 40000)

    def test_liq_price_1x_leverage_long(self):
        """At 1x leverage, LONG liq price should be near zero."""
        result = TradingBot.get_liquidation_price("BTCUSDT", 40000, 1, 1, "LONG")
        self.assertGreater(result["liquidation_price"], 0)
        # 1x leverage means nearly all of the price is the safety buffer
        self.assertAlmostEqual(
            result["liquidation_price"],
            40000 * (1 - 1 + 0.005),
            places=0,
        )

    def test_result_keys(self):
        result = TradingBot.get_liquidation_price("BTCUSDT", 40000, 1, 5, "LONG")
        for key in ("symbol", "side", "entry_price", "liquidation_price", "distance_pct", "leverage"):
            self.assertIn(key, result)


if __name__ == "__main__":
    unittest.main()
