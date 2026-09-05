import logging
import math
from datetime import date
from typing import Dict, List, Optional, Set
import yaml

from pm.ingest.broker_csv import BrokerCSVParser
from pm.ingest.markdown_signals import MarkdownSignalParser
from pm.market_data.pricing import MarketDataService
from pm.models import AllocationResult, ParsedSignal, ReconciliationSummary, SignalType
from pm.risk.clustering import CorrelationClusterEngine
from pm.risk.constraints import PortfolioConstraintOptimizer
from pm.engine.reconciler import PortfolioReconciler

logger = logging.getLogger(__name__)


class PortfolioManagerEngine:
    """
    Main controller orchestrating the 5-step Portfolio Management solver.
    """

    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        paths = self.config.get("paths", {})
        signals_cfg = self.config.get("signals", {})
        risk_cfg = self.config.get("risk", {})
        rebalance_cfg = self.config.get("rebalance", {})
        multipliers_cfg = self.config.get("multipliers", {})
        market_cap_cfg = self.config.get("market_cap_weighting", {})
        self.equivalent_symbols = self.config.get(
            "equivalent_symbols",
            [["GOOG", "GOOGL"], ["FOX", "FOXA"], ["BRK.A", "BRK.B"]],
        )

        self.reports_dir = paths.get("reports_dir", "/Users/matthewhope/reports")
        self.downloads_dir = paths.get("downloads_dir", "/Users/matthewhope/Downloads")
        self.obsidian_vault_dir = paths.get("obsidian_vault_dir", "/Users/matthewhope/reports")

        self.max_age_days = signals_cfg.get("max_age_days", 14)
        self.stale_warning_days = signals_cfg.get("stale_warning_days", 4)
        self.candidate_min_signal = SignalType.from_str(signals_cfg.get("candidate_min_signal", "OVERWEIGHT"))

        # Multipliers
        multipliers = {
            SignalType.OVERWEIGHT: multipliers_cfg.get("OVERWEIGHT", 1.5),
            SignalType.EQUAL_WEIGHT: multipliers_cfg.get("EQUAL_WEIGHT", 1.0),
            SignalType.UNDERWEIGHT: multipliers_cfg.get("UNDERWEIGHT", 0.5),
            SignalType.AVOID: multipliers_cfg.get("AVOID", 0.0),
        }

        # Subsystems
        self.signal_parser = MarkdownSignalParser(
            self.reports_dir,
            max_age_days=self.max_age_days,
            stale_warning_days=self.stale_warning_days,
        )
        self.csv_parser = BrokerCSVParser(self.downloads_dir)
        self.market_data = MarketDataService(lookback_days=risk_cfg.get("history_lookback_days", 180))
        self.cluster_engine = CorrelationClusterEngine(
            cophenetic_threshold=risk_cfg.get("clustering_cophenetic_threshold", 0.5)
        )
        self.enforce_cash_reserve = risk_cfg.get("enforce_cash_reserve_on_buys", True)
        self.optimizer = PortfolioConstraintOptimizer(
            max_position_weight=risk_cfg.get("max_position_weight", 0.15),
            min_cash_reserve=risk_cfg.get("min_cash_reserve", 0.15),
            max_cluster_exposure=risk_cfg.get("max_cluster_exposure", 0.25),
            multipliers=multipliers,
            market_cap_cfg=market_cap_cfg,
        )
        self.reconciler = PortfolioReconciler(
            relative_threshold=rebalance_cfg.get("relative_threshold", 0.20),
            hold_drift_tolerance=rebalance_cfg.get("hold_drift_tolerance", 1.00),
            min_dollar_trade=rebalance_cfg.get("min_dollar_trade", 1500.0),
            prefer_whole_shares=rebalance_cfg.get("prefer_whole_shares", True),
            liquidate_avoid=rebalance_cfg.get("liquidate_avoid", True),
        )

    def run_solver(
        self,
        csv_path: Optional[str] = None,
        as_of_date: Optional[date] = None,
    ) -> ReconciliationSummary:
        """
        Executes the complete deterministic 5-step capital allocation loop.
        """
        if as_of_date is None:
            as_of_date = date.today()

        # 1. Ingest Data
        logger.info("Ingesting broker CSV portfolio state...")
        portfolio_state = self.csv_parser.parse(csv_path)

        logger.info("Ingesting Markdown analyst signals from vault...")
        parsed_signals = self.signal_parser.parse_all_signals(as_of_date=as_of_date)

        # Resolve equivalent symbols (e.g. GOOG / GOOGL)
        # If any symbol in an equivalent group is held, canonical symbol is the held one
        held_symbols = set(portfolio_state.holdings.keys())
        alias_map: Dict[str, str] = {}
        for group in self.equivalent_symbols:
            held_in_group = [s for s in group if s in held_symbols]
            canonical = held_in_group[0] if held_in_group else group[0]
            for s in group:
                if s != canonical:
                    alias_map[s] = canonical

        # Merge signals from equivalent symbols
        for alias_sym, canonical_sym in alias_map.items():
            if alias_sym in parsed_signals:
                alias_sig = parsed_signals[alias_sym]
                if canonical_sym not in parsed_signals:
                    parsed_signals[canonical_sym] = alias_sig
                else:
                    can_sig = parsed_signals[canonical_sym]
                    if alias_sig.date >= can_sig.date or (alias_sig.signal == SignalType.OVERWEIGHT and can_sig.signal != SignalType.OVERWEIGHT):
                        logger.info(f"Adopting signal {alias_sig.signal.value} from equivalent symbol {alias_sym} for {canonical_sym}")
                        parsed_signals[canonical_sym] = alias_sig

        # Mark in_portfolio flag on signals
        for ticker, sig in parsed_signals.items():
            canonical = alias_map.get(ticker, ticker)
            sig.in_portfolio = canonical in portfolio_state.holdings

        # Identify Aging (Almost Stale) and Expired Signals
        aging_signals = [s for s in parsed_signals.values() if s.is_approaching_stale and s.ticker not in alias_map]
        aging_signals.sort(key=lambda s: (not s.in_portfolio, s.days_remaining))

        expired_signals = [s for s in parsed_signals.values() if s.is_expired and s.ticker not in alias_map]
        expired_signals.sort(key=lambda s: (not s.in_portfolio, -s.age_days))

        # 2. Form Investable Universe:
        # Current holdings + Non-portfolio candidates with active OVERWEIGHT signals (deduplicated against holdings)
        universe_tickers = set(portfolio_state.holdings.keys())
        for ticker, sig in parsed_signals.items():
            canonical = alias_map.get(ticker, ticker)
            if canonical in portfolio_state.holdings:
                continue
            if not sig.is_expired and sig.signal == self.candidate_min_signal:
                universe_tickers.add(canonical)

        sorted_universe = sorted(universe_tickers)
        logger.info(
            f"Total Investable Universe: {len(sorted_universe)} securities "
            f"({len(portfolio_state.holdings)} held, {len(sorted_universe) - len(portfolio_state.holdings)} candidates)."
        )

        # Map signals for all universe assets
        active_signals: Dict[str, SignalType] = {}
        for t in sorted_universe:
            if t in parsed_signals:
                sig_obj = parsed_signals[t]
                if sig_obj.is_expired:
                    active_signals[t] = SignalType.EQUAL_WEIGHT
                else:
                    active_signals[t] = sig_obj.signal
            else:
                active_signals[t] = SignalType.EQUAL_WEIGHT

        # 3. Step 1: Real-Time Pricing & Market Caps (yfinance)
        fallback_prices = {s: h.last_price for s, h in portfolio_state.holdings.items()}
        realtime_prices = self.market_data.fetch_realtime_prices(sorted_universe, fallback_prices=fallback_prices)
        market_caps = self.market_data.fetch_market_caps(sorted_universe)

        # Recalculate total portfolio value using live market prices
        current_equity_live = sum(
            portfolio_state.holdings[s].quantity * realtime_prices.get(s, portfolio_state.holdings[s].last_price)
            for s in portfolio_state.holdings
        )
        total_live_portfolio_value = current_equity_live + portfolio_state.cash_balance

        # 4. Step 2: Correlation Filtering (Clustering Engine)
        returns_df = self.market_data.fetch_historical_returns(sorted_universe, period="6mo")
        clusters = self.cluster_engine.cluster_assets(returns_df)

        # 5. Steps 3 & 4: Option 5 Market-Cap Base Weighting & Hard Constraints
        target_weights = self.optimizer.optimize_weights(
            tickers=sorted_universe,
            signals=active_signals,
            clusters=clusters,
            market_caps=market_caps,
        )

        # 6. Step 5: Reconciliation & Order Sizing
        allocations = self.reconciler.reconcile(
            total_portfolio_value=total_live_portfolio_value,
            current_holdings=portfolio_state.holdings,
            target_weights=target_weights,
            realtime_prices=realtime_prices,
            signals=active_signals,
            clusters=clusters,
        )

        # 7. Step 6: Dry Powder Cash Reserve Guard (Enforce Hard min_cash_reserve)
        # Guarantees that total buy orders do not deplete cash below the target cash reserve,
        # especially when existing winning positions are protected from selling.
        total_sells = sum(a.order_shares * a.realtime_price for a in allocations if a.action == "SELL")
        target_cash_reserve = self.optimizer.min_cash_reserve * total_live_portfolio_value
        
        if self.enforce_cash_reserve:
            available_buying_power = max(0.0, portfolio_state.cash_balance + total_sells - target_cash_reserve)
            total_buys_raw = sum(a.order_shares * a.realtime_price for a in allocations if a.action == "BUY")
            
            if total_buys_raw > available_buying_power and total_buys_raw > 0:
                scale = available_buying_power / total_buys_raw
                logger.info(
                    f"Enforcing cash reserve guard: Total buys (${total_buys_raw:,.2f}) exceed available buying power "
                    f"(${available_buying_power:,.2f}). Scaling buys by {scale:.4f} to preserve {self.optimizer.min_cash_reserve*100:.1f}% cash."
                )
                for a in allocations:
                    if a.action == "BUY" and a.order_shares > 0:
                        scaled_dollars = (a.order_shares * a.realtime_price) * scale
                        new_shares = math.floor(scaled_dollars / a.realtime_price)
                        if new_shares * a.realtime_price < self.reconciler.min_dollar_trade:
                            new_shares = 0
                        if new_shares > 0:
                            a.order_shares = float(new_shares)
                            a.reason += f" [CASH_GUARD_SCALE: {scale:.1%}]"
                        else:
                            a.action = "HOLD"
                            a.order_shares = 0.0
                            a.reason = "CASH_GUARD_SUB_FLOOR"

        # Final Cash Flow Summary
        total_buys = sum(a.order_shares * a.realtime_price for a in allocations if a.action == "BUY")
        total_sells = sum(a.order_shares * a.realtime_price for a in allocations if a.action == "SELL")
        projected_ending_cash = portfolio_state.cash_balance + total_sells - total_buys

        return ReconciliationSummary(
            total_portfolio_value=total_live_portfolio_value,
            current_cash=portfolio_state.cash_balance,
            target_cash_reserve=target_cash_reserve,
            projected_ending_cash=projected_ending_cash,
            total_buys_dollars=total_buys,
            total_sells_dollars=total_sells,
            allocations=allocations,
            clusters=clusters,
            aging_signals=aging_signals,
            expired_signals=expired_signals,
            max_age_days=self.max_age_days,
            execution_date=as_of_date,
        )
