from pm.engine.reconciler import PortfolioReconciler
from pm.models import Holding, SignalType


def test_reconciler_option_b_and_whole_shares():
    reconciler = PortfolioReconciler(
        relative_threshold=0.20,  # 20%
        min_dollar_trade=1500.0,  # $1,500
        prefer_whole_shares=True,
        liquidate_avoid=True,
        stepping_enabled=False,
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


def test_asymmetric_hold_drift_protects_winners():
    reconciler = PortfolioReconciler(
        relative_threshold=0.20,      # 20% standard threshold
        hold_drift_tolerance=1.00,    # 100% tolerance for EQUAL_WEIGHT (Hold)
        min_dollar_trade=1500.0,
        prefer_whole_shares=True,
        stepping_enabled=False,
    )

    total_value = 100000.0

    # Scenario: META is target $5,000 (5%), but current holdings are $8,750 (8.75%).
    # Absolute delta is -$3,750 (> $1,500 floor).
    # Relative drift is 3,750 / 5,000 = 75.0% (> 20% standard threshold, but <= 100% hold tolerance).
    holdings = {
        "META": Holding("META", "Meta Platforms", quantity=17.5, last_price=500.0, current_value=8750.0),
    }
    target_weights = {"META": 0.05}
    realtime_prices = {"META": 500.0}
    clusters = {1: ["META"]}

    # Case A: Signal is EQUAL_WEIGHT (Hold) -> Protected by asymmetric policy
    alloc_hold = reconciler.reconcile(
        total_portfolio_value=total_value,
        current_holdings=holdings,
        target_weights=target_weights,
        realtime_prices=realtime_prices,
        signals={"META": SignalType.EQUAL_WEIGHT},
        clusters=clusters,
    )
    res_hold = alloc_hold[0]
    assert res_hold.action == "HOLD"
    assert res_hold.order_shares == 0.0
    assert "HOLD_WINNER_PROTECTED" in res_hold.reason

    # Case B: Signal is UNDERWEIGHT -> Rebalance trim fires (not protected)
    alloc_under = reconciler.reconcile(
        total_portfolio_value=total_value,
        current_holdings=holdings,
        target_weights=target_weights,
        realtime_prices=realtime_prices,
        signals={"META": SignalType.UNDERWEIGHT},
        clusters=clusters,
    )
    res_under = alloc_under[0]
    assert res_under.action == "SELL"
    assert res_under.order_shares == 8.0  # $3,750 / $500 = 7.5 -> round to 8
    assert "REBALANCE_TRIM" in res_under.reason

    # Case C: Drift exceeds 100% (e.g. Current $11,000 vs Target $5,000 = 120% drift) -> Trim fires
    holdings_extreme = {
        "META": Holding("META", "Meta Platforms", quantity=22.0, last_price=500.0, current_value=11000.0),
    }
    alloc_extreme = reconciler.reconcile(
        total_portfolio_value=total_value,
        current_holdings=holdings_extreme,
        target_weights=target_weights,
        realtime_prices=realtime_prices,
        signals={"META": SignalType.EQUAL_WEIGHT},
        clusters=clusters,
    )
    res_extreme = alloc_extreme[0]
    assert res_extreme.action == "SELL"
    assert res_extreme.order_shares == 12.0  # $6,000 / $500 = 12
    assert "REBALANCE_TRIM" in res_extreme.reason


def test_transaction_stepping_and_caps():
    reconciler = PortfolioReconciler(
        relative_threshold=0.20,
        hold_drift_tolerance=1.00,
        min_dollar_trade=1500.0,
        prefer_whole_shares=True,
        liquidate_avoid=True,
        stepping_enabled=True,
        starter_step_factor=0.50,
        rebalance_step_factor=0.50,
        max_trade_dollar_cap=6000.0,
        avoid_step_factor=1.00,
    )

    total_value = 100000.0  # $100,000

    holdings = {
        # Existing Buy (Scale Up): Current $4,000 (4%), Target $10,000 (10%). Delta = +$6,000.
        # Stepped 50% = $3,000 (<= $6,000 cap, >= $1,500 floor). Price $200 -> BUY 15 shares.
        "UP": Holding("UP", "Scale Up Corp", quantity=20, last_price=200.0, current_value=4000.0),

        # Existing Trim (Scale Down): Current $14,000 (14%), Target $6,000 (6%). Delta = -$8,000.
        # Stepped 50% = $4,000 (<= $6,000 cap, >= $1,500 floor). Price $500 -> SELL 8 shares.
        "DOWN": Holding("DOWN", "Scale Down Corp", quantity=28, last_price=500.0, current_value=14000.0),

        # Sub-Floor Stepped Delta: Current $3,000 (3%), Target $5,000 (5%). Delta = +$2,000.
        # Stepped 50% = $1,000 (< $1,500 floor) -> HOLD suppressed.
        "SMALL": Holding("SMALL", "Small Delta Corp", quantity=30, last_price=100.0, current_value=3000.0),

        # AVOID Exit: Current 15.5 shares @ $100 = $1,550. Target 0. Signal AVOID.
        # Immediate 100% liquidation -> SELL 15.5 shares.
        "BAD": Holding("BAD", "Bad Corp", quantity=15.5, last_price=100.0, current_value=1550.0),
    }

    target_weights = {
        "UP": 0.10,
        "DOWN": 0.06,
        "SMALL": 0.05,
        "BAD": 0.00,
        # Jumbo Starter Entry: Target $16,000 (16%). 50% step = $8,000.
        # Capped at $6,000! Price $1,000 -> BUY 6 whole shares ($6,000 / $1,000 = 6).
        "JUMBO": 0.16,
    }

    realtime_prices = {
        "UP": 200.0,
        "DOWN": 500.0,
        "SMALL": 100.0,
        "BAD": 100.0,
        "JUMBO": 1000.0,
    }

    signals = {
        "UP": SignalType.OVERWEIGHT,
        "DOWN": SignalType.UNDERWEIGHT,
        "SMALL": SignalType.OVERWEIGHT,
        "BAD": SignalType.AVOID,
        "JUMBO": SignalType.OVERWEIGHT,
    }

    clusters = {1: ["UP"], 2: ["DOWN"], 3: ["SMALL"], 4: ["BAD"], 5: ["JUMBO"]}

    allocations = reconciler.reconcile(
        total_portfolio_value=total_value,
        current_holdings=holdings,
        target_weights=target_weights,
        realtime_prices=realtime_prices,
        signals=signals,
        clusters=clusters,
    )

    alloc_map = {a.ticker: a for a in allocations}

    # 1. JUMBO starter buy: 50% of $16,000 is $8,000, clipped to $6,000 cap -> 6 shares
    assert alloc_map["JUMBO"].action == "BUY"
    assert alloc_map["JUMBO"].order_shares == 6.0
    assert "STARTER_TRANCHE" in alloc_map["JUMBO"].reason
    assert "cap $6,000" in alloc_map["JUMBO"].reason

    # 2. UP rebalance buy: 50% of $6,000 delta = $3,000 / $200 = 15 shares
    assert alloc_map["UP"].action == "BUY"
    assert alloc_map["UP"].order_shares == 15.0
    assert "REBALANCE_BUY_STEP" in alloc_map["UP"].reason

    # 3. DOWN rebalance trim: 50% of $8,000 delta = $4,000 / $500 = 8 shares
    assert alloc_map["DOWN"].action == "SELL"
    assert alloc_map["DOWN"].order_shares == 8.0
    assert "REBALANCE_TRIM_STEP" in alloc_map["DOWN"].reason

    # 4. SMALL delta: 50% of $2,000 delta = $1,000 < $1,500 floor -> suppressed to HOLD
    assert alloc_map["SMALL"].action == "HOLD"
    assert alloc_map["SMALL"].order_shares == 0.0
    assert "SUPPRESSED_UNDER_FLOOR" in alloc_map["SMALL"].reason

    # 5. BAD exit: AVOID signal -> 100% full liquidation of all 15.5 shares
    assert alloc_map["BAD"].action == "SELL"
    assert alloc_map["BAD"].order_shares == 15.5
    assert alloc_map["BAD"].reason == "AVOID_LIQUIDATION"


