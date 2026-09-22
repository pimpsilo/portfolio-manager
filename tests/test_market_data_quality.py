from unittest.mock import patch

from pm.market_data.pricing import MarketDataService
from pm.risk.constraints import PortfolioConstraintOptimizer
from pm.models import SignalType


def test_market_data_tracks_csv_price_fallback():
    service = MarketDataService()

    with patch("pm.market_data.pricing.yf.Tickers", side_effect=RuntimeError("offline")):
        prices = service.fetch_realtime_prices(["AAPL"], fallback_prices={"AAPL": 123.45})

    assert prices == {"AAPL": 123.45}
    assert service.price_sources["AAPL"] == "CSV_FALLBACK"


def test_unknown_market_cap_is_neutral_and_visible():
    service = MarketDataService()

    with patch("pm.market_data.pricing.yf.Tickers", side_effect=RuntimeError("offline")):
        caps = service.fetch_market_caps(["UNKNOWN"])

    assert caps == {"UNKNOWN": 0.0}
    assert service.market_cap_sources["UNKNOWN"] == "UNKNOWN"


def test_market_cap_tier_boundaries_are_explicit():
    optimizer = PortfolioConstraintOptimizer(
        market_cap_cfg={
            "enabled": True,
            "mega_cap_threshold": 200e9,
            "large_cap_threshold": 50e9,
            "tier_multipliers": {"mega_cap": 3.0, "large_cap": 1.8, "mid_cap": 1.0},
        }
    )

    assert optimizer.get_cap_tier_multiplier(200e9) == 3.0
    assert optimizer.get_cap_tier_multiplier(50e9) == 1.8
    assert optimizer.get_cap_tier_multiplier(49.999e9) == 1.0
