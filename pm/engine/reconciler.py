import logging
from typing import Dict, List, Optional
from pm.models import AllocationResult, Holding, SignalType

logger = logging.getLogger(__name__)


class PortfolioReconciler:
    """
    Reconciles target portfolio weights with current broker holdings
    using Option B (Relative Drift + Minimum Trade Floor) and Whole-Share Sizing.
    """

    def __init__(
        self,
        relative_threshold: float = 0.20,      # 20% relative drift
        hold_drift_tolerance: float = 1.00,    # 100% relative drift tolerance for EQUAL_WEIGHT (Hold)
        min_dollar_trade: float = 1500.0,       # $1,500 minimum trade floor
        prefer_whole_shares: bool = True,
        liquidate_avoid: bool = True,
        stepping_enabled: bool = True,
        starter_step_factor: float = 0.50,
        rebalance_step_factor: float = 0.50,
        max_trade_dollar_cap: float = 6000.0,
        avoid_step_factor: float = 1.00,
    ):
        self.relative_threshold = relative_threshold
        self.hold_drift_tolerance = hold_drift_tolerance
        self.min_dollar_trade = min_dollar_trade
        self.prefer_whole_shares = prefer_whole_shares
        self.liquidate_avoid = liquidate_avoid
        self.stepping_enabled = stepping_enabled
        self.starter_step_factor = starter_step_factor
        self.rebalance_step_factor = rebalance_step_factor
        self.max_trade_dollar_cap = max_trade_dollar_cap
        self.avoid_step_factor = avoid_step_factor

    def reconcile(
        self,
        total_portfolio_value: float,
        current_holdings: Dict[str, Holding],
        target_weights: Dict[str, float],
        realtime_prices: Dict[str, float],
        signals: Dict[str, SignalType],
        clusters: Dict[int, List[str]],
    ) -> List[AllocationResult]:
        """
        Calculates trade orders and allocation metrics for all assets in the universe.
        """
        # Invert clusters: ticker -> cluster_id
        ticker_to_cluster: Dict[str, int] = {}
        for c_id, members in clusters.items():
            for m in members:
                ticker_to_cluster[m] = c_id

        all_tickers = sorted(set(list(current_holdings.keys()) + list(target_weights.keys())))
        results: List[AllocationResult] = []

        for ticker in all_tickers:
            holding = current_holdings.get(ticker)
            current_shares = holding.quantity if holding else 0.0
            price = realtime_prices.get(ticker, 0.0)
            if price <= 0 and holding:
                price = holding.last_price

            current_value = current_shares * price
            current_weight = current_value / total_portfolio_value if total_portfolio_value > 0 else 0.0

            target_weight = target_weights.get(ticker, 0.0)
            target_value = round(target_weight * total_portfolio_value, 2)
            dollar_delta = round(target_value - current_value, 2)

            sig = signals.get(ticker, SignalType.EQUAL_WEIGHT)
            cluster_id = ticker_to_cluster.get(ticker, 0)

            # Rebalance logic
            action = "HOLD"
            order_shares = 0.0
            is_whole_share = True
            reason = "ALIGNED"

            abs_delta_dollars = abs(dollar_delta)

            # 1. Full Liquidation on AVOID or Zero Target
            if (sig == SignalType.AVOID or target_weight <= 0.0) and current_shares > 0:
                action = "SELL"
                if self.avoid_step_factor >= 1.0 or self.liquidate_avoid:
                    order_shares = current_shares
                else:
                    raw_shares = current_shares * self.avoid_step_factor
                    order_shares = round(raw_shares) if self.prefer_whole_shares else raw_shares
                is_whole_share = order_shares.is_integer()
                reason = "AVOID_LIQUIDATION" if sig == SignalType.AVOID else "ZERO_TARGET_LIQUIDATION"

            # 2. New Position Entry (Candidate with current_shares == 0)
            elif current_shares <= 0:
                effective_starter_dollars = target_value
                if self.stepping_enabled:
                    effective_starter_dollars = round(min(
                        target_value * self.starter_step_factor,
                        self.max_trade_dollar_cap,
                    ), 2)

                if effective_starter_dollars >= self.min_dollar_trade and price > 0:
                    action = "BUY"
                    raw_shares = effective_starter_dollars / price
                    order_shares = round(raw_shares) if self.prefer_whole_shares else raw_shares
                    reason = (
                        f"STARTER_TRANCHE ({self.starter_step_factor*100:.0f}%, cap ${self.max_trade_dollar_cap:,.0f})"
                        if self.stepping_enabled
                        else "NEW_STARTER_ENTRY"
                    )
                else:
                    action = "HOLD"
                    order_shares = 0.0
                    reason = (
                        f"BELOW_MIN_STARTER_FLOOR (${effective_starter_dollars:.0f} < ${self.min_dollar_trade:.0f})"
                        if self.stepping_enabled
                        else "BELOW_MIN_STARTER_FLOOR"
                    )

            # 3. Existing Position Rebalance (Option B: Relative Drift + Trade Floor)
            else:
                rel_drift = abs_delta_dollars / target_value if target_value > 0 else 1.0

                # Determine effective drift tolerance:
                # If holding is above target but analyst rating is EQUAL_WEIGHT (Hold),
                # allow it to float up to hold_drift_tolerance before forcing trims.
                if sig == SignalType.EQUAL_WEIGHT and dollar_delta < 0:
                    effective_threshold = self.hold_drift_tolerance
                    is_hold_policy = True
                else:
                    effective_threshold = self.relative_threshold
                    is_hold_policy = False

                if abs_delta_dollars < self.min_dollar_trade:
                    action = "HOLD"
                    order_shares = 0.0
                    reason = f"SUPPRESSED_UNDER_FLOOR (${abs_delta_dollars:.0f} < ${self.min_dollar_trade:.0f})"
                elif rel_drift <= effective_threshold:
                    action = "HOLD"
                    order_shares = 0.0
                    if is_hold_policy:
                        reason = f"HOLD_WINNER_PROTECTED ({rel_drift*100:.1f}% <= {effective_threshold*100:.0f}%)"
                    else:
                        reason = f"SUPPRESSED_IN_BAND ({rel_drift*100:.1f}% <= {effective_threshold*100:.0f}%)"
                else:
                    # Triggers active rebalance!
                    if dollar_delta > 0:
                        effective_buy_dollars = abs_delta_dollars
                        if self.stepping_enabled:
                            effective_buy_dollars = round(min(
                                abs_delta_dollars * self.rebalance_step_factor,
                                self.max_trade_dollar_cap,
                            ), 2)
                        if effective_buy_dollars >= self.min_dollar_trade and price > 0:
                            action = "BUY"
                            raw_shares = effective_buy_dollars / price
                            order_shares = round(raw_shares) if self.prefer_whole_shares else raw_shares
                            reason = (
                                f"REBALANCE_BUY_STEP ({self.rebalance_step_factor*100:.0f}%, cap ${self.max_trade_dollar_cap:,.0f})"
                                if self.stepping_enabled
                                else f"REBALANCE_BUY ({rel_drift*100:.1f}% drift)"
                            )
                        else:
                            action = "HOLD"
                            order_shares = 0.0
                            reason = f"SUPPRESSED_UNDER_FLOOR (stepped ${effective_buy_dollars:.0f} < ${self.min_dollar_trade:.0f})"
                    else:
                        effective_sell_dollars = abs_delta_dollars
                        if self.stepping_enabled:
                            effective_sell_dollars = round(min(
                                abs_delta_dollars * self.rebalance_step_factor,
                                self.max_trade_dollar_cap,
                            ), 2)
                        if effective_sell_dollars >= self.min_dollar_trade and price > 0:
                            action = "SELL"
                            raw_shares = effective_sell_dollars / price
                            order_shares = round(raw_shares) if self.prefer_whole_shares else raw_shares
                            # Guard: cannot sell more than current shares
                            if order_shares >= current_shares:
                                order_shares = current_shares
                                is_whole_share = current_shares.is_integer()
                            reason = (
                                f"REBALANCE_TRIM_STEP ({self.rebalance_step_factor*100:.0f}%, cap ${self.max_trade_dollar_cap:,.0f})"
                                if self.stepping_enabled
                                else f"REBALANCE_TRIM ({rel_drift*100:.1f}% drift)"
                            )
                        else:
                            action = "HOLD"
                            order_shares = 0.0
                            reason = f"SUPPRESSED_UNDER_FLOOR (stepped ${effective_sell_dollars:.0f} < ${self.min_dollar_trade:.0f})"


            # Final check: If price is so high that order_shares == 0, revert to HOLD
            if action in ("BUY", "SELL") and order_shares <= 0:
                action = "HOLD"
                order_shares = 0.0
                reason = "SUB_SHARE_MINIMUM"

            drift_pct = (target_weight - current_weight) * 100.0

            results.append(
                AllocationResult(
                    ticker=ticker,
                    current_shares=current_shares,
                    realtime_price=price,
                    current_value=current_value,
                    current_weight=current_weight * 100.0,
                    signal=sig,
                    cluster_id=cluster_id,
                    base_weight=0.0,  # Populated in controller
                    target_weight=target_weight * 100.0,
                    target_value=target_value,
                    dollar_delta=dollar_delta,
                    drift_pct=drift_pct,
                    action=action,
                    order_shares=order_shares,
                    is_whole_share=is_whole_share,
                    reason=reason,
                )
            )

        # Sort: Active Actions (SELL then BUY) first by absolute dollar delta, then HOLDs
        action_order = {"SELL": 0, "BUY": 1, "HOLD": 2}
        results.sort(key=lambda r: (action_order.get(r.action, 3), -abs(r.dollar_delta)))

        return results
