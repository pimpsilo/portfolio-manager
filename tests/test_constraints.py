from pm.models import SignalType
from pm.risk.constraints import PortfolioConstraintOptimizer


def test_hard_constraints_and_caps():
    tickers = ["AAPL", "MSFT", "GOOG", "AMZN", "NVDA", "BADCO"]
    signals = {
        "AAPL": SignalType.OVERWEIGHT,   # 1.5x
        "MSFT": SignalType.OVERWEIGHT,   # 1.5x
        "GOOG": SignalType.EQUAL_WEIGHT, # 1.0x
        "AMZN": SignalType.EQUAL_WEIGHT, # 1.0x
        "NVDA": SignalType.UNDERWEIGHT,  # 0.5x
        "BADCO": SignalType.AVOID,       # 0.0x
    }
    clusters = {
        1: ["AAPL", "MSFT"],
        2: ["GOOG", "AMZN"],
        3: ["NVDA"],
        4: ["BADCO"],
    }

    optimizer = PortfolioConstraintOptimizer(
        max_position_weight=0.15,   # 15% max single position
        min_cash_reserve=0.10,      # 10% min cash
        max_cluster_exposure=0.25,  # 25% max cluster
    )

    weights = optimizer.optimize_weights(tickers, signals, clusters)

    # 1. BADCO with AVOID signal must be strictly 0%
    assert weights["BADCO"] == 0.0

    # 2. No single position may exceed 15%
    for t, w in weights.items():
        assert w <= 0.150001, f"{t} exceeded 15% cap: {w}"

    # 3. No cluster may exceed 25%
    cluster_1_sum = weights["AAPL"] + weights["MSFT"]
    assert cluster_1_sum <= 0.250001, f"Cluster 1 exceeded 25% cap: {cluster_1_sum}"
    cluster_2_sum = weights["GOOG"] + weights["AMZN"]
    assert cluster_2_sum <= 0.250001, f"Cluster 2 exceeded 25% cap: {cluster_2_sum}"

    # 4. Total equity weight must not violate 10% minimum cash (sum <= 90%)
    total_equity = sum(weights.values())
    assert total_equity <= 0.900001, f"Total equity violated 10% cash: {total_equity}"
    assert total_equity > 0.55


def test_diversified_portfolio_full_budget():
    # 10 diversified assets across 5 clusters
    tickers = [f"STK{i}" for i in range(10)]
    signals = {t: SignalType.OVERWEIGHT if i < 5 else SignalType.EQUAL_WEIGHT for i, t in enumerate(tickers)}
    clusters = {
        1: ["STK0", "STK1"],
        2: ["STK2", "STK3"],
        3: ["STK4", "STK5"],
        4: ["STK6", "STK7"],
        5: ["STK8", "STK9"],
    }

    optimizer = PortfolioConstraintOptimizer(
        max_position_weight=0.15,
        min_cash_reserve=0.10,
        max_cluster_exposure=0.25,
    )

    weights = optimizer.optimize_weights(tickers, signals, clusters)
    total_equity = sum(weights.values())

    assert abs(total_equity - 0.90) < 1e-4
    for w in weights.values():
        assert w <= 0.150001


def test_option_5_market_cap_tiering():
    # Test Option 5: Mega-Cap ($200B+) gets 3.0x, Large ($50B-$200B) gets 1.8x, Mid (<$50B) gets 1.0x
    tickers = ["MEGA_STOCK", "LARGE_STOCK", "MID_STOCK"]
    signals = {
        "MEGA_STOCK": SignalType.OVERWEIGHT,   # 1.5x * 3.0 = 4.5
        "LARGE_STOCK": SignalType.OVERWEIGHT,  # 1.5x * 1.8 = 2.7
        "MID_STOCK": SignalType.OVERWEIGHT,    # 1.5x * 1.0 = 1.5
    }
    clusters = {1: ["MEGA_STOCK"], 2: ["LARGE_STOCK"], 3: ["MID_STOCK"]}
    market_caps = {
        "MEGA_STOCK": 1000e9,  # $1T
        "LARGE_STOCK": 100e9,  # $100B
        "MID_STOCK": 15e9,     # $15B
    }

    optimizer = PortfolioConstraintOptimizer(
        max_position_weight=0.50, # generous for testing proportions
        min_cash_reserve=0.10,
        max_cluster_exposure=0.50,
        market_cap_cfg={
            "enabled": True,
            "mega_cap_threshold": 200e9,
            "large_cap_threshold": 50e9,
            "tier_multipliers": {
                "mega_cap": 3.0,
                "large_cap": 1.8,
                "mid_cap": 1.0,
            },
        },
    )

    weights = optimizer.optimize_weights(tickers, signals, clusters, market_caps=market_caps)

    # Ratio of MEGA to MID should be 3.0x (4.5 / 1.5 = 3.0)
    ratio_mega_to_mid = weights["MEGA_STOCK"] / weights["MID_STOCK"]
    assert abs(ratio_mega_to_mid - 3.0) < 1e-3, f"Expected 3.0 ratio, got {ratio_mega_to_mid}"

    # Ratio of LARGE to MID should be 1.8x (2.7 / 1.5 = 1.8)
    ratio_large_to_mid = weights["LARGE_STOCK"] / weights["MID_STOCK"]
    assert abs(ratio_large_to_mid - 1.8) < 1e-3, f"Expected 1.8 ratio, got {ratio_large_to_mid}"

    # Total equity allocated is 90% (10% cash reserve)
    assert abs(sum(weights.values()) - 0.90) < 1e-4
