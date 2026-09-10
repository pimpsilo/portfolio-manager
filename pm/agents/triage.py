import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
import yaml

from pm.ingest.broker_csv import BrokerCSVParser
from pm.ingest.markdown_signals import MarkdownSignalParser
from pm.models import SignalType, TriageItem, TriagePlan

logger = logging.getLogger(__name__)


class SignalTriageEngine:
    """
    Audits current portfolio holdings, the watchlist, and existing research
    reports in Obsidian to determine which tickers require LLM multi-agent
    evaluation and in what priority order.
    """

    def __init__(self, config_or_path: Any = "config.yaml"):
        if isinstance(config_or_path, dict):
            self.config = config_or_path
            self.config_dir = Path(__file__).resolve().parents[2]
        else:
            cfg_path = Path(config_or_path).resolve()
            self.config_dir = cfg_path.parent
            with open(cfg_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f)

        paths = self.config.get("paths", {})
        signals_cfg = self.config.get("signals", {})
        self.watchlist_cfg = self.config.get("watchlist", {})
        self.equivalent_symbols = self.config.get(
            "equivalent_symbols",
            [["GOOG", "GOOGL"], ["FOX", "FOXA"], ["BRK.A", "BRK.B"]],
        )

        self.reports_dir = paths.get("reports_dir", "/Users/matthewhope/reports")
        self.downloads_dir = paths.get("downloads_dir", "/Users/matthewhope/Downloads")

        self.max_age_days = signals_cfg.get("max_age_days", 14)
        self.stale_warning_days = signals_cfg.get("stale_warning_days", 4)
        self.excluded_tickers = set(signals_cfg.get("excluded_tickers", []))

        self.signal_parser = MarkdownSignalParser(
            self.reports_dir,
            max_age_days=self.max_age_days,
            stale_warning_days=self.stale_warning_days,
        )
        self.csv_parser = BrokerCSVParser(self.downloads_dir)

    def _get_canonical_symbol(self, ticker: str, held_symbols: Set[str]) -> str:
        """Map equivalent share classes (e.g. GOOG / GOOGL) to the held or primary ticker."""
        for group in self.equivalent_symbols:
            if ticker in group:
                held_in_group = [s for s in group if s in held_symbols]
                return held_in_group[0] if held_in_group else group[0]
        return ticker

    def run_triage(
        self,
        csv_path: Optional[str] = None,
        extra_tickers: Optional[List[str]] = None,
        as_of_date: Optional[date] = None,
        min_age_days: Optional[int] = None,
        category: Optional[str] = None,
    ) -> TriagePlan:
        """
        Executes signal triage by comparing portfolio holdings and watchlist
        against reports in the vault.
        """
        if as_of_date is None:
            as_of_date = date.today()

        # 1. Parse current portfolio holdings
        portfolio_state = self.csv_parser.parse(csv_path)
        held_symbols = set(portfolio_state.holdings.keys())

        # 2. Parse all existing research signals
        parsed_signals = self.signal_parser.parse_all_signals(as_of_date=as_of_date)

        # 3. Build watchlist metadata index
        categories = self.watchlist_cfg.get("categories", {})
        ticker_meta: Dict[str, Dict[str, Any]] = {}

        # 3a. Ingest external stocks_file if configured
        stocks_file_val = self.watchlist_cfg.get("stocks_file")
        if stocks_file_val:
            s_path = Path(stocks_file_val)
            if not s_path.is_absolute():
                s_path = (self.config_dir / s_path).resolve()
            if s_path.exists():
                for line in s_path.read_text(encoding="utf-8-sig").splitlines():
                    t = line.strip().lstrip("\ufeff").upper()
                    if t and not t.startswith("#"):
                        ticker_meta[t] = {
                            "category": "stocks_watchlist",
                            "asset_type": "stock",
                            "analysts": ["market", "social", "news", "fundamentals"],
                        }

        # 3b. Ingest explicit category groups (overriding or supplementing)
        for cat_name, cat_data in categories.items():
            asset_type = cat_data.get("asset_type", "stock")
            analysts = cat_data.get("analysts", ["market", "news", "fundamentals"])
            for t in cat_data.get("tickers", []):
                t_clean = t.strip().upper()
                ticker_meta[t_clean] = {
                    "category": cat_name,
                    "asset_type": asset_type,
                    "analysts": analysts,
                }

        # Also incorporate any extra tickers specified on the command line
        if extra_tickers:
            for t in extra_tickers:
                t_clean = t.strip().upper()
                if t_clean and t_clean not in ticker_meta:
                    ticker_meta[t_clean] = {
                        "category": "manual_request",
                        "asset_type": "stock",
                        "analysts": ["market", "social", "news", "fundamentals"],
                    }

        # Ensure all currently held positions are tracked
        for held_ticker in held_symbols:
            if held_ticker not in ticker_meta:
                ticker_meta[held_ticker] = {
                    "category": "portfolio_holding",
                    "asset_type": "stock",
                    "analysts": ["market", "social", "news", "fundamentals"],
                }

        # 4. Triage each ticker
        triage_items: List[TriageItem] = []
        excluded_tickers = getattr(self, "excluded_tickers", set())
        for ticker, meta in sorted(ticker_meta.items()):
            canonical = self._get_canonical_symbol(ticker, held_symbols)
            is_held = canonical in held_symbols or ticker in held_symbols

            if canonical in excluded_tickers or ticker in excluded_tickers:
                logger.info(f"Skipping excluded ticker from triage: {ticker}")
                continue

            # Check if signal exists (check both raw and canonical symbol)
            sig = parsed_signals.get(ticker) or parsed_signals.get(canonical)

            if sig is None:
                # No research report on file
                if is_held:
                    priority = 1
                    status = "MISSING"
                    reason = "Held position with no research report in vault"
                else:
                    priority = 2
                    status = "MISSING"
                    reason = "Watchlist candidate with no research report"

                triage_items.append(
                    TriageItem(
                        ticker=ticker,
                        category=meta["category"],
                        asset_type=meta["asset_type"],
                        analysts=meta["analysts"],
                        in_portfolio=is_held,
                        status=status,
                        priority=priority,
                        reason=reason,
                    )
                )
            else:
                # Report exists - check aging / expiration / min_age_days
                age_days = sig.age_days
                days_remaining = sig.days_remaining

                if min_age_days is not None:
                    if age_days >= min_age_days:
                        priority = 1 if is_held else 3
                        status = "AGE_THRESHOLD"
                        reason = f"{'Held position' if is_held else 'Watchlist candidate'} report is {age_days}d old (threshold: {min_age_days}d)"
                    else:
                        priority = 5
                        status = "FRESH"
                        reason = f"Fresh report ({age_days}d old < {min_age_days}d threshold)"
                else:
                    if sig.is_expired:
                        if is_held:
                            priority = 1
                            status = "EXPIRED"
                            reason = f"Held position with EXPIRED report ({age_days}d old)"
                        else:
                            priority = 4
                            status = "EXPIRED"
                            reason = f"Watchlist candidate with EXPIRED report ({age_days}d old)"
                    elif sig.is_approaching_stale:
                        if is_held:
                            priority = 1
                            status = "STALE_SOON"
                            reason = f"Held position with report STALE SOON ({days_remaining}d remaining)"
                        else:
                            priority = 3
                            status = "STALE_SOON"
                            reason = f"Watchlist candidate with report STALE SOON ({days_remaining}d remaining)"
                    else:
                        priority = 5
                        status = "FRESH"
                        reason = f"Active valid report ({age_days}d old, {days_remaining}d remaining)"

                triage_items.append(
                    TriageItem(
                        ticker=ticker,
                        category=meta["category"],
                        asset_type=meta["asset_type"],
                        analysts=meta["analysts"],
                        in_portfolio=is_held,
                        status=status,
                        priority=priority,
                        age_days=age_days,
                        days_remaining=days_remaining,
                        current_signal=sig.signal,
                        reason=reason,
                    )
                )

        # Optional category filter
        if category:
            cat_clean = category.strip().lower()
            triage_items = [it for it in triage_items if it.category.lower() == cat_clean]

        return TriagePlan(items=triage_items, execution_date=as_of_date)

    @staticmethod
    def format_triage_table(plan: TriagePlan) -> str:
        """Formats the triage plan as human-readable CLI tables."""
        output_lines = []

        counts = plan.summary_counts
        total_tracked = len(plan.items)
        queue_len = len(plan.queue)

        output_lines.append(f"📊 Tracked Securities : {total_tracked}")
        output_lines.append(f"⚡ Evaluation Queue   : {queue_len} tickers needing research")
        stat_parts = [f"   - Missing Reports : {counts.get('MISSING', 0)}"]
        if "AGE_THRESHOLD" in counts:
            stat_parts.append(f"   - Age Threshold   : {counts.get('AGE_THRESHOLD', 0)}")
        else:
            stat_parts.append(f"   - Expired (>14d)  : {counts.get('EXPIRED', 0)}")
            stat_parts.append(f"   - Stale Soon (<=4d): {counts.get('STALE_SOON', 0)}")
        stat_parts.append(f"   - Fresh           : {counts.get('FRESH', 0)}")
        output_lines.append("\n".join(stat_parts))

        if plan.queue:
            output_lines.append("\n=======================================================")
            output_lines.append(f"📋 ACTIONABLE RESEARCH QUEUE ({queue_len} Tickers)")
            output_lines.append("=======================================================")
            headers = ["Priority", "Ticker", "Category", "Type", "In Port?", "Status", "Current Rating", "Reason"]
            rows = []
            for it in plan.queue:
                p_label = "P1 (CRITICAL)" if it.priority == 1 else f"P{it.priority}"
                in_port_str = "YES" if it.in_portfolio else "No"
                rating_str = it.current_signal.value if it.current_signal else "None"
                rows.append([
                    p_label,
                    it.ticker,
                    it.category,
                    it.asset_type,
                    in_port_str,
                    it.status,
                    rating_str,
                    it.reason,
                ])

            col_widths = [len(h) for h in headers]
            for row in rows:
                for i, cell in enumerate(row):
                    col_widths[i] = max(col_widths[i], len(str(cell)))

            header_line = " | ".join(f"{h:<{col_widths[i]}}" for i, h in enumerate(headers))
            sep_line = "-+-".join("-" * col_widths[i] for i in range(len(headers)))
            row_lines = [
                " | ".join(f"{str(cell):<{col_widths[i]}}" for i, cell in enumerate(row))
                for row in rows
            ]
            output_lines.append("\n".join([header_line, sep_line] + row_lines))

        if plan.fresh_items:
            output_lines.append("\n=======================================================")
            output_lines.append(f"✅ FRESH REPORTS ON FILE ({len(plan.fresh_items)} Tickers)")
            output_lines.append("=======================================================")
            f_headers = ["Ticker", "Category", "In Port?", "Age", "Days Left", "Rating"]
            f_rows = []
            for it in sorted(plan.fresh_items, key=lambda x: x.ticker):
                in_port_str = "YES" if it.in_portfolio else "No"
                rating_str = it.current_signal.value if it.current_signal else "None"
                f_rows.append([
                    it.ticker,
                    it.category,
                    in_port_str,
                    f"{it.age_days}d",
                    f"{it.days_remaining}d",
                    rating_str,
                ])

            col_widths = [len(h) for h in f_headers]
            for row in f_rows:
                for i, cell in enumerate(row):
                    col_widths[i] = max(col_widths[i], len(str(cell)))

            header_line = " | ".join(f"{h:<{col_widths[i]}}" for i, h in enumerate(f_headers))
            sep_line = "-+-".join("-" * col_widths[i] for i in range(len(f_headers)))
            row_lines = [
                " | ".join(f"{str(cell):<{col_widths[i]}}" for i, cell in enumerate(row))
                for row in f_rows
            ]
            output_lines.append("\n".join([header_line, sep_line] + row_lines))

        return "\n".join(output_lines)
