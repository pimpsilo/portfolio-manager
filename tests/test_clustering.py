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
