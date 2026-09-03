import logging
from typing import Dict, List
from pm.models import SignalType

logger = logging.getLogger(__name__)


class PortfolioConstraintOptimizer:
    """
    Computes target portfolio weights applying base signal multipliers,
    single position caps, cluster exposure limits, and minimum cash reserve.
    """

    DEFAULT_MULTIPLIERS = {
        SignalType.OVERWEIGHT: 1.5,
        SignalType.EQUAL_WEIGHT: 1.0,
        SignalType.UNDERWEIGHT: 0.5,
        SignalType.AVOID: 0.0,
    }

    def __init__(
        self,
        max_position_weight: float = 0.15,      # 15%
        min_cash_reserve: float = 0.10,         # 10%
        max_cluster_exposure: float = 0.25,     # 25%
        multipliers: Dict[SignalType, float] = None,
    ):
        self.max_position_weight = max_position_weight
        self.min_cash_reserve = min_cash_reserve
        self.max_cluster_exposure = max_cluster_exposure
        self.multipliers = multipliers or self.DEFAULT_MULTIPLIERS

    def optimize_weights(
        self,
        tickers: List[str],
        signals: Dict[str, SignalType],
        clusters: Dict[int, List[str]],
    ) -> Dict[str, float]:
        """
        Executes iterative constrained weight optimization.
        Returns: {ticker: target_weight_fraction}
        """
        if not tickers:
            return {}

        max_equity_budget = max(0.0, 1.0 - self.min_cash_reserve)

        # 1. Base weighting from signal multipliers
        raw_weights: Dict[str, float] = {}
        for t in tickers:
            sig = signals.get(t, SignalType.EQUAL_WEIGHT)
            mult = self.multipliers.get(sig, 1.0)
            raw_weights[t] = float(mult)

        total_raw = sum(raw_weights.values())
        if total_raw <= 0:
            # All avoid or zero
            return {t: 0.0 for t in tickers}

        # Normalize to available equity budget
        weights = {t: (raw_weights[t] / total_raw) * max_equity_budget for t in tickers}

        # Invert clusters for fast lookup: ticker -> cluster_id
        ticker_to_cluster: Dict[str, int] = {}
        for c_id, members in clusters.items():
            for m in members:
                ticker_to_cluster[m] = c_id

        # 2. Iterative projection & re-normalization loop
        # Alternately enforces single-position caps and cluster caps
        for iteration in range(50):
            violation = False
            locked_assets = set()

            # Constraint A: Single Position Cap (15%)
            for t, w in weights.items():
                if signals.get(t) == SignalType.AVOID:
                    weights[t] = 0.0
                    locked_assets.add(t)
                elif w > self.max_position_weight + 1e-6:
                    weights[t] = self.max_position_weight
                    locked_assets.add(t)
                    violation = True

            # Constraint B: Cluster Exposure Cap (25%)
            for c_id, members in clusters.items():
                active_members = [m for m in members if m in weights]
                cluster_sum = sum(weights[m] for m in active_members)
                if cluster_sum > self.max_cluster_exposure + 1e-6:
                    scale = self.max_cluster_exposure / cluster_sum
                    for m in active_members:
                        weights[m] *= scale
                        locked_assets.add(m)
                    violation = True

            # Re-normalize remaining eligible assets if equity budget has room
            current_total = sum(weights.values())
            if not violation and current_total <= max_equity_budget + 1e-5:
                break

            # If total exceeds equity budget, rescale
            if current_total > max_equity_budget:
                unlocked = [t for t in tickers if t not in locked_assets and weights[t] > 0]
                if unlocked:
                    excess = current_total - max_equity_budget
                    unlocked_sum = sum(weights[t] for t in unlocked)
                    if unlocked_sum > 0:
                        reduce_ratio = max(0.0, (unlocked_sum - excess) / unlocked_sum)
                        for t in unlocked:
                            weights[t] *= reduce_ratio
                else:
                    # Uniformly scale down everything
                    scale = max_equity_budget / current_total
                    for t in tickers:
                        weights[t] *= scale
                    break

        # Final verification & rounding precision
        final_weights: Dict[str, float] = {}
        for t, w in weights.items():
            final_weights[t] = max(0.0, round(w, 6))

        return final_weights
