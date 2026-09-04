import logging
from typing import Dict, List, Optional
from pm.models import SignalType

logger = logging.getLogger(__name__)


class PortfolioConstraintOptimizer:
    """
    Implements hard mathematical portfolio constraints:
    - Single position cap: <= 15%
    - Minimum cash reserve: >= 10%
    - Correlated cluster exposure cap: <= 25%
    - Option 5 Market-Cap / Maturity Anchor Weighting (Mega-Cap 3.0x, Large 1.8x, Mid 1.0x)
    - Iterative proportional re-normalization of residual weight
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
        multipliers: Optional[Dict[SignalType, float]] = None,
        market_cap_cfg: Optional[Dict] = None,
    ):
        self.max_position_weight = max_position_weight
        self.min_cash_reserve = min_cash_reserve
        self.max_cluster_exposure = max_cluster_exposure
        self.multipliers = multipliers or self.DEFAULT_MULTIPLIERS
        self.market_cap_cfg = market_cap_cfg or {}

    def get_cap_tier_multiplier(self, market_cap: Optional[float]) -> float:
        """
        Determines the institutional maturity anchor multiplier based on market cap.
        Mega-Cap ($200B+): 3.0x base anchor
        Large-Cap ($50B - $200B): 1.8x base
        Mid/Emerging (<$50B): 1.0x base
        """
        if not self.market_cap_cfg.get("enabled", False) or not market_cap:
            return 1.0

        mega_thresh = self.market_cap_cfg.get("mega_cap_threshold", 200e9)
        large_thresh = self.market_cap_cfg.get("large_cap_threshold", 50e9)
        tier_mults = self.market_cap_cfg.get("tier_multipliers", {})

        mega_m = tier_mults.get("mega_cap", 3.0)
        large_m = tier_mults.get("large_cap", 1.8)
        mid_m = tier_mults.get("mid_cap", 1.0)

        if market_cap >= mega_thresh:
            return float(mega_m)
        elif market_cap >= large_thresh:
            return float(large_m)
        else:
            return float(mid_m)

    def optimize_weights(
        self,
        tickers: List[str],
        signals: Dict[str, SignalType],
        clusters: Dict[int, List[str]],
        market_caps: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """
        Executes iterative constrained weight optimization applying Market-Cap Anchoring
        and analyst signal multipliers.
        Returns: {ticker: target_weight_fraction}
        """
        if not tickers:
            return {}

        market_caps = market_caps or {}
        max_equity_budget = max(0.0, 1.0 - self.min_cash_reserve)

        # 1. Base weighting from Market-Cap Tier * Signal Multipliers
        raw_weights: Dict[str, float] = {}
        for t in tickers:
            sig = signals.get(t, SignalType.EQUAL_WEIGHT)
            sig_mult = self.multipliers.get(sig, 1.0)
            if sig == SignalType.AVOID:
                raw_weights[t] = 0.0
                continue

            cap_mult = self.get_cap_tier_multiplier(market_caps.get(t))
            raw_weights[t] = float(cap_mult * sig_mult)

        total_raw = sum(raw_weights.values())
        if total_raw <= 0:
            return {t: 0.0 for t in tickers}

        # Normalize to available equity budget (<= 90%)
        weights = {t: (v / total_raw) * max_equity_budget for t, v in raw_weights.items()}

        # 2. Iterative Constraint Enforcement (Caps on single position and clusters)
        # Max iterations to converge
        for iteration in range(10):
            violation_found = False

            # Check 1: Single Position Cap (15%)
            for t in list(weights.keys()):
                if weights[t] > self.max_position_weight:
                    weights[t] = self.max_position_weight
                    violation_found = True

            # Check 2: Cluster Exposure Cap (25%)
            for c_id, members in clusters.items():
                active_members = [m for m in members if m in weights and weights[m] > 0]
                if not active_members:
                    continue

                cluster_total = sum(weights[m] for m in active_members)
                if cluster_total > self.max_cluster_exposure + 1e-6:
                    scale_factor = self.max_cluster_exposure / cluster_total
                    for m in active_members:
                        weights[m] *= scale_factor
                    violation_found = True

            # Check 3: Budget check
            current_total = sum(weights.values())
            if current_total > max_equity_budget + 1e-6:
                excess = current_total - max_equity_budget
                # Scale down uncapped non-avoid positions
                uncapped = [
                    t for t in weights
                    if weights[t] > 0 and weights[t] < self.max_position_weight - 1e-5
                ]
                if uncapped:
                    uncapped_sum = sum(weights[t] for t in uncapped)
                    if uncapped_sum > excess:
                        scale = (uncapped_sum - excess) / uncapped_sum
                        for t in uncapped:
                            weights[t] *= scale
                violation_found = True

            if not violation_found:
                break

        # Final sanity check: strictly zero for AVOID
        for t in tickers:
            if signals.get(t) == SignalType.AVOID:
                weights[t] = 0.0

        # Round very small epsilon weights
        weights = {t: round(w, 6) if w > 1e-5 else 0.0 for t, w in weights.items()}
        return weights
