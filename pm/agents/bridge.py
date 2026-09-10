import copy
from dataclasses import dataclass
from datetime import datetime
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import yaml

from pm.models import TriageItem

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True))
    # Check sibling TradingAgents .env fallback
    _sibling_env = Path(__file__).resolve().parents[3] / "TradingAgents" / ".env"
    if _sibling_env.exists():
        load_dotenv(_sibling_env, override=False)
except ImportError:
    pass

logger = logging.getLogger(__name__)


@dataclass
class AgentEvaluationResult:
    ticker: str
    signal: Optional[str]
    report_path: Optional[str]
    success: bool
    error_message: Optional[str] = None
    duration_seconds: float = 0.0


class TradingAgentsBridge:
    """
    Bridge interfacing portfolio-manager with the TradingAgents multi-agent
    framework to execute research evaluations and save Obsidian-compatible reports.
    """

    def __init__(self, config_or_path: Any = "config.yaml", debug: bool = False):
        if isinstance(config_or_path, dict):
            self.config = config_or_path
        else:
            with open(config_or_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f)

        paths = self.config.get("paths", {})
        self.reports_dir = Path(paths.get("reports_dir", "/Users/matthewhope/reports"))
        self.debug = debug

        self.tradingagents_cfg = self.config.get("tradingagents", {})
        self.watchlist_cfg = self.config.get("watchlist", {})
        self.rate_limit_delay = float(self.watchlist_cfg.get("rate_limit_delay_seconds", 2.0))

    def _build_agent_config(self) -> Dict[str, Any]:
        """Merges portfolio-manager config into TradingAgents DEFAULT_CONFIG."""
        try:
            from tradingagents.default_config import DEFAULT_CONFIG
            base_config = copy.deepcopy(DEFAULT_CONFIG)
        except ImportError:
            base_config = {}

        # Ensure reports are written inside reports_dir so MarkdownSignalParser finds them
        reports_target = str(self.reports_dir)
        base_config["results_dir"] = reports_target
        try:
            os.makedirs(reports_target, exist_ok=True)
        except OSError:
            pass

        # Apply tradingagents section overrides from config.yaml
        for key, value in self.tradingagents_cfg.items():
            if value is not None:
                base_config[key] = value

        return base_config

    def evaluate_ticker(
        self,
        ticker: str,
        trade_date: Optional[str] = None,
        asset_type: str = "stock",
        analysts: Optional[List[str]] = None,
    ) -> AgentEvaluationResult:
        """
        Runs the TradingAgentsGraph pipeline for a single ticker and writes
        the report tree directly to the Obsidian vault reports directory.
        """
        start_time = time.time()
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y-%m-%d")

        if analysts is None:
            analysts = ["market", "news", "fundamentals"]

        ticker_clean = ticker.strip().upper()
        logger.info(f"🚀 Starting TradingAgents research for {ticker_clean} (asset_type={asset_type}, analysts={analysts})...")

        try:
            from tradingagents.graph.trading_graph import TradingAgentsGraph

            agent_config = self._build_agent_config()
            graph = TradingAgentsGraph(
                selected_analysts=analysts,
                debug=self.debug,
                config=agent_config,
            )

            final_state, signal = graph.propagate(
                company_name=ticker_clean,
                trade_date=trade_date,
                asset_type=asset_type,
            )

            # Save the full report tree into reports_dir/<TICKER>/<TICKER>_<YYYYMMDD>_<HHMMSS>/
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target_save_path = Path(self.reports_dir) / ticker_clean / f"{ticker_clean}_{stamp}"
            report_path = graph.save_reports(final_state, ticker_clean, save_path=target_save_path)
            duration = time.time() - start_time

            signal_val = str(signal.value) if hasattr(signal, "value") else str(signal)
            logger.info(f"✅ Finished research for {ticker_clean}: Signal={signal_val} in {duration:.1f}s")

            return AgentEvaluationResult(
                ticker=ticker_clean,
                signal=signal_val,
                report_path=str(report_path),
                success=True,
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.time() - start_time
            logger.exception(f"❌ Failed to run research pipeline for {ticker_clean}: {e}")
            return AgentEvaluationResult(
                ticker=ticker_clean,
                signal=None,
                report_path=None,
                success=False,
                error_message=str(e),
                duration_seconds=duration,
            )

    def run_batch(
        self,
        queue: List[TriageItem],
        limit: Optional[int] = None,
    ) -> List[AgentEvaluationResult]:
        """
        Executes evaluations for a list of triaged candidate items with rate limiting
        and error recovery.
        """
        targets = queue[:limit] if limit is not None and limit > 0 else queue
        total = len(targets)
        results: List[AgentEvaluationResult] = []

        if total == 0:
            logger.info("Evaluation queue is empty. No tickers to evaluate.")
            return results

        print(f"\n=======================================================")
        print(f"🤖 TRADINGAGENTS PIPELINE DISPATCH ({total} Tickers)")
        print(f"=======================================================")

        for idx, item in enumerate(targets, 1):
            print(f"[{idx}/{total}] Evaluating {item.ticker} ({item.category}, {item.asset_type})...")
            res = self.evaluate_ticker(
                ticker=item.ticker,
                asset_type=item.asset_type,
                analysts=item.analysts,
            )
            results.append(res)

            if res.success:
                prior_str = f" (Prior: {item.current_signal.value})" if item.current_signal else ""
                print(f"       ➡️  Result: {res.signal}{prior_str} ({res.duration_seconds:.1f}s) -> {res.report_path}")
            else:
                print(f"       ⚠️  Failed: {res.error_message}")

            # Inter-ticker pause to avoid burst rate-limits on LLM APIs
            if idx < total and self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)

        success_count = sum(1 for r in results if r.success)
        print("=======================================================")
        print(f"🏁 Batch complete: {success_count}/{total} successful.")

        changed = []
        for r, item in zip(results, targets):
            if r.success and item.current_signal and r.signal != item.current_signal.value:
                changed.append(f"   • {item.ticker}: {item.current_signal.value} ➡️  {r.signal}")
        if changed:
            print("\n🔄 Rating Changes Detected:")
            for ch in changed:
                print(ch)

        print("=======================================================\n")

        return results
