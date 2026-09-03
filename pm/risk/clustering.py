import logging
from typing import Dict, List
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

logger = logging.getLogger(__name__)


class CorrelationClusterEngine:
    """
    Computes return correlation matrix and groups assets into correlated clusters
    using SciPy hierarchical clustering.
    """

    def __init__(self, cophenetic_threshold: float = 0.5, method: str = "average"):
        self.cophenetic_threshold = cophenetic_threshold
        self.method = method

    def cluster_assets(self, returns_df: pd.DataFrame) -> Dict[int, List[str]]:
        """
        Takes a DataFrame of asset daily returns and partitions tickers into clusters.
        Returns: {cluster_id: [ticker1, ticker2, ...]}
        """
        if returns_df.empty or len(returns_df.columns) <= 1:
            # Trivial case: single cluster or empty
            return {1: list(returns_df.columns)}

        # Filter out assets with zero variance or all NaNs
        valid_cols = [c for c in returns_df.columns if returns_df[c].std() > 1e-8]
        if len(valid_cols) <= 1:
            return {1: list(returns_df.columns)}

        clean_returns = returns_df[valid_cols].dropna(how="all")
        corr_matrix = clean_returns.corr().fillna(0.0).clip(-1.0, 1.0)

        # Distance metric: D = sqrt(0.5 * (1 - C))
        # When C = 1 (perfect correlation), D = 0
        # When C = 0 (uncorrelated), D = ~0.707
        # When C = -1 (inverse correlation), D = 1.0
        dist_matrix = np.sqrt(np.clip(0.5 * (1.0 - corr_matrix.values), 0.0, 1.0))
        np.fill_diagonal(dist_matrix, 0.0)

        # Convert to condensed 1D distance array for scipy linkage
        condensed_dist = squareform(dist_matrix, checks=False)

        # Hierarchical Linkage
        Z = linkage(condensed_dist, method=self.method)

        # Form flat clusters
        labels = fcluster(Z, t=self.cophenetic_threshold, criterion="distance")

        clusters: Dict[int, List[str]] = {}
        for ticker, label in zip(valid_cols, labels):
            c_id = int(label)
            if c_id not in clusters:
                clusters[c_id] = []
            clusters[c_id].append(ticker)

        # Any dropped columns (e.g. no price history or zero variance) get their own individual cluster
        dropped = set(returns_df.columns) - set(valid_cols)
        next_id = max(clusters.keys(), default=0) + 1
        for d in dropped:
            clusters[next_id] = [d]
            next_id += 1

        logger.info(f"Formed {len(clusters)} correlated asset clusters from {len(returns_df.columns)} tickers.")
        return clusters
