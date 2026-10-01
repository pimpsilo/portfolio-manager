import numpy as np
import pandas as pd
from pm.risk.clustering import CorrelationClusterEngine


def test_hierarchical_clustering():
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=100)

    # Correlated cluster 1: AAPL & MSFT (tech)
    base_tech = np.random.normal(0.001, 0.02, 100)
    aapl_ret = base_tech + np.random.normal(0, 0.005, 100)
    msft_ret = base_tech + np.random.normal(0, 0.005, 100)

    # Correlated cluster 2: HIG & CINF (insurance)
    base_ins = np.random.normal(0.0005, 0.01, 100)
    hig_ret = base_ins + np.random.normal(0, 0.003, 100)
    cinf_ret = base_ins + np.random.normal(0, 0.003, 100)

    df = pd.DataFrame(
        {
            "AAPL": aapl_ret,
            "MSFT": msft_ret,
            "HIG": hig_ret,
            "CINF": cinf_ret,
        },
        index=dates,
    )

    engine = CorrelationClusterEngine(cophenetic_threshold=0.5)
    clusters = engine.cluster_assets(df)

    assert len(clusters) >= 2

    # Check that AAPL and MSFT are grouped together
    aapl_cluster = None
    msft_cluster = None
    for c_id, members in clusters.items():
        if "AAPL" in members:
            aapl_cluster = c_id
        if "MSFT" in members:
            msft_cluster = c_id

    assert aapl_cluster is not None
    assert aapl_cluster == msft_cluster


def test_maxclust_target_clusters():
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=100)
    data = {}
    for i in range(12):
        data[f"STK{i}"] = np.random.normal(0.001, 0.02, 100)

    df = pd.DataFrame(data, index=dates)
    engine = CorrelationClusterEngine(k=5, criterion="maxclust")
    clusters = engine.cluster_assets(df)

    assert len(clusters) == 5
    all_assigned = [t for members in clusters.values() for t in members]
    assert sorted(all_assigned) == sorted(df.columns)


def test_cluster_rationalization_n_leq_4():
    # 6 stocks in cluster 1
    clusters = {
        1: ["AMD", "MU", "NVDA", "ASML", "VRT", "CIEN"],
        2: ["COST", "EG"],
    }
    dates = pd.date_range("2026-01-01", periods=100)
    np.random.seed(42)
    returns = {t: np.random.normal(0.001, 0.02, 100) for t in clusters[1] + clusters[2]}
    returns_df = pd.DataFrame(returns, index=dates)

    market_caps = {
        "NVDA": 5_000_000_000_000,
        "AMD": 1_000_000_000_000,
        "MU": 1_000_000_000_000,
        "ASML": 700_000_000_000,
        "VRT": 90_000_000_000,
        "CIEN": 50_000_000_000,
        "COST": 400_000_000_000,
        "EG": 14_000_000_000,
    }
    signals = {t: "OVERWEIGHT" for t in market_caps}
    prices = {t: 100.0 for t in market_caps}
    tps = {t: 120.0 for t in market_caps}

    engine = CorrelationClusterEngine(k=2, max_assets_per_cluster=4, concentration_guide="composite")
    rationalized, excluded = engine.rationalize_clusters(
        clusters=clusters,
        returns_df=returns_df,
        market_caps=market_caps,
        signals=signals,
        realtime_prices=prices,
        target_prices=tps,
        max_assets=4,
    )

    # Cluster 1 must have exactly 4 stocks
    assert len(rationalized[1]) == 4
    # The 2 mid/large caps with lowest score should be excluded
    assert len(excluded) == 2
    assert "CIEN" in excluded
    assert "VRT" in excluded
    # Cluster 2 has 2 stocks <= 4, should remain untouched
    assert len(rationalized[2]) == 2
    assert sorted(rationalized[2]) == ["COST", "EG"]


def test_compute_cluster_stats():
    clusters = {
        1: ["NVDA", "AMD"],
        2: ["COST"],
    }
    corr_df = pd.DataFrame(
        [
            [1.0, 0.65, 0.10],
            [0.65, 1.0, 0.15],
            [0.10, 0.15, 1.0],
        ],
        index=["NVDA", "AMD", "COST"],
        columns=["NVDA", "AMD", "COST"],
    )
    weights = {"NVDA": 0.15, "AMD": 0.10, "COST": 0.08}

    engine = CorrelationClusterEngine(k=2)
    stats = engine.compute_cluster_stats(clusters, corr_matrix=corr_df, target_weights=weights)

    assert stats[1]["member_count"] == 2
    assert stats[1]["anchor_lead"] == "NVDA"
    assert round(stats[1]["avg_intra_corr"], 2) == 0.65

    assert stats[2]["member_count"] == 1
    assert stats[2]["anchor_lead"] == "COST"

