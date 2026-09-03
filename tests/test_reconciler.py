from pm.engine.reconciler import PortfolioReconciler
from pm.models import Holding, SignalType


def test_reconciler_option_b_and_whole_shares():
    reconciler = PortfolioReconciler(
        relative_threshold=0.20,  # 20%
        min_dollar_trade=1500.0,  # $1,500
        prefer_whole_shares=True,
        liquidate_avoid=True,
    )

    total_value = 100000.0  # $100,000

    holdings = {
        # Current: $5,000 (5.0%). Target: 4.5% ($4,500). Delta: -$500. Under $1,500 floor -> HOLD
        "AAPL": Holding("AAPL", "Apple", quantity=25, last_price=200.0, current_value=5000.0),

        # Current: $10,000 (10.0%). Target: 7.0% ($7,000). Delta: -$3,000. Rel drift: 3000/7000 = 42.8% > 20% -> SELL
        "MSFT": Holding("MSFT", "Microsoft", quantity=20, last_price=500.0, current_value=10000.0),

        # Current: 15.004 shares @ $100 = $1,500.40. Signal: AVOID -> Complete liquidation (SELL 15.004)
        "JUNK": Holding("JUNK", "Junk Corp", quantity=15.004, last_price=100.0, current_value=1500.40),

        # Current: $4,000. Target: $4,600. Delta: +$600. Under $1,500 floor -> HOLD
        "GOOG": Holding("GOOG", "Alphabet", quantity=40, last_price=100.0, current_value=4000.0),
    }

    target_weights = {
        "AAPL": 0.045, # 4.5%
        "MSFT": 0.070, # 7.0%
        "JUNK": 0.000, # 0.0%
        "GOOG": 0.046, # 4.6%
        "NEWCO": 0.030, # 3.0% ($3,000 target on unheld candidate)
    }

    realtime_prices = {
        "AAPL": 200.0,
        "MSFT": 500.0,
        "JUNK": 100.0,
        "GOOG": 100.0,
        "NEWCO": 150.0,
    }

    signals = {
        "AAPL": SignalType.EQUAL_WEIGHT,
        "MSFT": SignalType.UNDERWEIGHT,
        "JUNK": SignalType.AVOID,
        "GOOG": SignalType.EQUAL_WEIGHT,
        "NEWCO": SignalType.OVERWEIGHT,
    }

    clusters = {1: ["AAPL", "MSFT"], 2: ["GOOG"], 3: ["JUNK"], 4: ["NEWCO"]}

    allocations = reconciler.reconcile(
        total_portfolio_value=total_value,
        current_holdings=holdings,
        target_weights=target_weights,
        realtime_prices=realtime_prices,
        signals=signals,
        clusters=clusters,
    )

    alloc_map = {a.ticker: a for a in allocations}

    # 1. AAPL: Delta -$500 is under $1,500 floor -> HOLD
    assert alloc_map["AAPL"].action == "HOLD"
    assert alloc_map["AAPL"].order_shares == 0.0

    # 2. MSFT: Delta -$3,000 is > $1,500 and 42.8% drift -> SELL 6 whole shares ($3,000 / $500 = 6)
    assert alloc_map["MSFT"].action == "SELL"
    assert alloc_map["MSFT"].order_shares == 6.0
    assert alloc_map["MSFT"].is_whole_share is True

    # 3. JUNK: AVOID signal -> Full liquidation of exact fractional quantity 15.004
    assert alloc_map["JUNK"].action == "SELL"
    assert alloc_map["JUNK"].order_shares == 15.004
    assert alloc_map["JUNK"].is_whole_share is False

    # 4. GOOG: Delta +$600 is under $1,500 floor -> HOLD
    assert alloc_map["GOOG"].action == "HOLD"

    # 5. NEWCO: New position, target $3,000 >= $1,500 -> BUY 20 whole shares ($3,000 / $150 = 20)
    assert alloc_map["NEWCO"].action == "BUY"
    assert alloc_map["NEWCO"].order_shares == 20.0
    assert alloc_map["NEWCO"].is_whole_share is True
