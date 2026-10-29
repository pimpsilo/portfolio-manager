import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

logger = logging.getLogger(__name__)


class CorrelationClusterEngine:
    """
    Hierarchical Return Correlation Clustering & Intra-Cluster Concentration Engine.
    Aligned with Correlation_Analyzer methodology:
      - Pearson correlation distance: D = clip(1 - r, 0, 2)
      - Average linkage hierarchical tree
      - Dynamic tree pruning: criterion='maxclust' (target k clusters) or 'distance'
      - Intra-cluster stock rationalization: capping n <= max_assets_per_cluster
    """

    def __init__(
        self,
        k: int = 10,
        criterion: Optional[str] = None,
        method: str = "average",
        concentration_guide: str = "composite",
        max_assets_per_cluster: Optional[int] = 4,
        distance_threshold: float = 0.70,
        cophenetic_threshold: Optional[float] = None,
        overflow_policy: str = "share",
    ):
        if criterion is not None:
            self.criterion = criterion
        elif cophenetic_threshold is not None:
            self.criterion = "distance"
        else:
            self.criterion = "maxclust"

        self.k = k
        self.method = method
        self.concentration_guide = concentration_guide
        self.max_assets_per_cluster = max_assets_per_cluster
        self.distance_threshold = distance_threshold
        self.cophenetic_threshold = cophenetic_threshold
        self.overflow_policy = overflow_policy

        # Backward compatibility for legacy cophenetic_threshold argument
        if cophenetic_threshold is not None:
            self.distance_threshold = cophenetic_threshold

        self.last_corr_matrix: pd.DataFrame = pd.DataFrame()
        self.last_clusters: Dict[int, List[str]] = {}

    def cluster_assets(self, returns_df: pd.DataFrame) -> Dict[int, List[str]]:
        """
        Takes a DataFrame of daily asset percentage returns and partitions tickers into clusters.
        Uses Pearson correlation distance: D = clip(1 - C, 0, 2).
        Returns: {cluster_id: [ticker1, ticker2, ...]}
        """
        if returns_df.empty or len(returns_df.columns) <= 1:
            clusters = {1: list(returns_df.columns)}
            self.last_clusters = clusters
            return clusters

        # Filter out assets with zero variance or all NaNs
        valid_cols = [c for c in returns_df.columns if returns_df[c].std() > 1e-8]
        if len(valid_cols) <= 1:
            clusters = {1: list(returns_df.columns)}
            self.last_clusters = clusters
            return clusters

        clean_returns = returns_df[valid_cols].dropna(how="all")
        corr_matrix = clean_returns.corr().fillna(0.0).clip(-1.0, 1.0)
        self.last_corr_matrix = corr_matrix

        # Distance metric: D = clip(1 - C, 0, 2) (Correlation_Analyzer parity)
        dist_matrix = np.clip(1.0 - corr_matrix.values, 0.0, 2.0)
        dist_matrix = (dist_matrix + dist_matrix.T) / 2.0
        np.fill_diagonal(dist_matrix, 0.0)

        # Convert to condensed 1D distance array for scipy linkage
        condensed_dist = squareform(dist_matrix, checks=False)

        # Hierarchical Linkage
        Z = linkage(condensed_dist, method=self.method)

        # Form flat clusters
        if self.criterion == "maxclust":
            target_k = max(1, min(self.k, len(valid_cols)))
            labels = fcluster(Z, t=target_k, criterion="maxclust")
        else:
            labels = fcluster(Z, t=self.distance_threshold, criterion="distance")

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

        self.last_clusters = clusters
        logger.info(
            f"Formed {len(clusters)} correlated asset clusters from {len(returns_df.columns)} tickers "
            f"(method={self.method}, criterion={self.criterion})."
        )
        return clusters

    def _score_member(
        self,
        t: str,
        signals: Dict[str, Any],
        market_caps: Dict[str, float],
        returns_df: pd.DataFrame,
        realtime_prices: Dict[str, float],
        target_prices: Dict[str, Optional[float]],
        concentration_guide: str,
    ) -> float:
        sig_obj = signals.get(t)
        sig_val = getattr(sig_obj, "value", str(sig_obj)) if sig_obj else "EQUAL_WEIGHT"
        if sig_val == "OVERWEIGHT":
            sig_pts = 2.0
        elif sig_val == "EQUAL_WEIGHT":
            sig_pts = 1.0
        else:
            sig_pts = 0.0

        mc = market_caps.get(t, 0.0)
        if mc >= 200_000_000_000:
            cap_pts = 3.0
        elif mc >= 50_000_000_000:
            cap_pts = 2.0
        else:
            cap_pts = 1.0

        sharpe = 0.0
        if t in returns_df and not returns_df[t].dropna().empty:
            series = returns_df[t].dropna()
            if len(series) >= 20 and series.std() > 1e-8:
                sharpe = float((series.mean() * 252) / (series.std() * np.sqrt(252)))

        upside_pct = 0.0
        tp = target_prices.get(t)
        price = realtime_prices.get(t, 0.0)
        if tp and price > 0:
            upside_pct = float((tp - price) / price * 100.0)

        if concentration_guide == "sharpe":
            return (sig_pts * 100.0) + sharpe
        elif concentration_guide == "upside":
            return (sig_pts * 100.0) + upside_pct
        elif concentration_guide == "market_cap":
            log_mc = math.log10(max(1.0, mc))
            return (sig_pts * 100.0) + log_mc
        else:
            return (sig_pts * 100.0) + (cap_pts * 10.0) + sharpe + (0.1 * upside_pct)

    def rationalize_clusters(
        self,
        clusters: Dict[int, List[str]],
        returns_df: pd.DataFrame,
        market_caps: Dict[str, float],
        signals: Dict[str, Any],
        realtime_prices: Dict[str, float],
        target_prices: Optional[Dict[str, Optional[float]]] = None,
        max_assets: Optional[int] = None,
        guide: Optional[str] = None,
        held_tickers: Optional[Set[str]] = None,
        overflow_policy: Optional[str] = None,
    ) -> Tuple[Dict[int, List[str]], Set[str]]:
        """
        Rationalizes clusters so that no single cluster has more than max_assets.
        In 'share' overflow policy with held_tickers provided:
          - Retains all existing holdings in the cluster (cluster cap 25% is shared proportionally)
          - Blocks new non-held candidate entrants if cluster capacity is reached
        In 'drop' policy (or held_tickers=None):
          - Drops lowest-scoring members to strictly enforce n <= max_assets.

        Returns: (rationalized_clusters, set_of_excluded_tickers)
        """
        max_n = max_assets if max_assets is not None else self.max_assets_per_cluster
        if max_n is None or max_n <= 0:
            return clusters, set()

        concentration_guide = guide or self.concentration_guide or "composite"
        policy = overflow_policy or getattr(self, "overflow_policy", "share")
        target_prices = target_prices or {}

        rationalized_clusters: Dict[int, List[str]] = {}
        excluded_tickers: Set[str] = set()

        for c_id, members in sorted(clusters.items()):
            if len(members) <= max_n:
                rationalized_clusters[c_id] = list(members)
                continue

            # Soft-cap weight sharing when holdings are provided
            if policy == "share" and held_tickers is not None:
                held_in_cluster = [t for t in members if t in held_tickers]
                candidates_in_cluster = [t for t in members if t not in held_tickers]

                if len(held_in_cluster) >= max_n:
                    # Retain all held assets with shared cluster cap; block all new candidates
                    rationalized_clusters[c_id] = list(held_in_cluster)
                    for c in candidates_in_cluster:
                        excluded_tickers.add(c)
                    logger.info(
                        f"Cluster {c_id} has {len(held_in_cluster)} held assets (>= max {max_n}). "
                        f"Retaining all held assets with shared 25% cap; blocked {len(candidates_in_cluster)} candidate entrants."
                    )
                    continue
                else:
                    open_slots = max_n - len(held_in_cluster)
                    candidate_scores = [
                        (self._score_member(t, signals, market_caps, returns_df, realtime_prices, target_prices, concentration_guide), t)
                        for t in candidates_in_cluster
                    ]
                    candidate_scores.sort(key=lambda x: x[0], reverse=True)
                    retained_cands = [t for _, t in candidate_scores[:open_slots]]
                    dropped_cands = [t for _, t in candidate_scores[open_slots:]]

                    rationalized_clusters[c_id] = held_in_cluster + retained_cands
                    for d in dropped_cands:
                        excluded_tickers.add(d)
                    logger.info(
                        f"Cluster {c_id} has {len(held_in_cluster)} held assets (< max {max_n}). "
                        f"Admitted top {len(retained_cands)} candidates, blocked {len(dropped_cands)}."
                    )
                    continue

            # Legacy/default drop behavior: rank all members and drop beyond max_n
            scores = [
                (self._score_member(t, signals, market_caps, returns_df, realtime_prices, target_prices, concentration_guide), t)
                for t in members
            ]
            scores.sort(key=lambda x: x[0], reverse=True)
            retained = [t for _, t in scores[:max_n]]
            dropped = [t for _, t in scores[max_n:]]

            rationalized_clusters[c_id] = retained
            for d in dropped:
                excluded_tickers.add(d)

            logger.info(
                f"Cluster {c_id} rationalized from {len(members)} to {len(retained)} stocks "
                f"(kept: {retained}, dropped: {dropped} via guide='{concentration_guide}')."
            )

        return rationalized_clusters, excluded_tickers

    def compute_cluster_stats(
        self,
        clusters: Dict[int, List[str]],
        corr_matrix: Optional[pd.DataFrame] = None,
        target_weights: Optional[Dict[str, float]] = None,
    ) -> Dict[int, Dict[str, Any]]:
        """
        Computes intra-cluster analytics:
          - Member count
          - Lead / anchor ticker (highest target weight or first member)
          - Average intra-cluster correlation
          - Min and Max correlation among members
        """
        c_matrix = corr_matrix if corr_matrix is not None else self.last_corr_matrix
        weights = target_weights or {}
        details: Dict[int, Dict[str, Any]] = {}

        for c_id, members in clusters.items():
            if not members:
                continue

            # Find lead anchor (highest target weight)
            lead = max(members, key=lambda m: weights.get(m, 0.0))
            lead_wt = weights.get(lead, 0.0)

            # Calculate pairwise correlations among cluster members
            avg_corr = 1.0
            min_corr = 1.0
            max_corr = 1.0

            if len(members) > 1 and not c_matrix.empty:
                valid_m = [m for m in members if m in c_matrix.columns]
                if len(valid_m) > 1:
                    sub = c_matrix.loc[valid_m, valid_m]
                    upper_vals = sub.values[np.triu_indices_from(sub.values, k=1)]
                    if len(upper_vals) > 0:
                        avg_corr = float(np.mean(upper_vals))
                        min_corr = float(np.min(upper_vals))
                        max_corr = float(np.max(upper_vals))

            details[c_id] = {
                "member_count": len(members),
                "members": members,
                "anchor_lead": lead,
                "anchor_lead_weight": lead_wt,
                "avg_intra_corr": avg_corr,
                "min_intra_corr": min_corr,
                "max_intra_corr": max_corr,
            }

        return details
