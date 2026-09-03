import re
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional
from pm.models import ParsedSignal, SignalType


class MarkdownSignalParser:
    """
    Parses tradingagents markdown reports and dashboard tables to extract
    ticker recommendations, dates, and price targets.
    """

    FOLDER_PATTERN = re.compile(r"^([A-Z0-9]+)_(\d{8})_(\d{6})")
    RATING_PATTERN = re.compile(r"\*\*(?:Rating|Recommendation)\*\*:\s*([^\n\r*]+)", re.IGNORECASE)
    TARGET_PATTERN = re.compile(r"\*\*Price Target\*\*:\s*([0-9.,]+)", re.IGNORECASE)
    STOP_PATTERN = re.compile(r"\*\*Stop(?:-|\s*)Loss\*\*:\s*([0-9.,]+)", re.IGNORECASE)

    def __init__(self, reports_dir: str, max_age_days: int = 14, stale_warning_days: int = 4):
        self.reports_dir = Path(reports_dir)
        self.max_age_days = max_age_days
        self.stale_warning_days = stale_warning_days

    def parse_all_signals(self, as_of_date: Optional[date] = None) -> Dict[str, ParsedSignal]:
        """
        Recursively scans reports_dir, extracts signals from the newest report for each ticker,
        and marks expiration and approaching stale status.
        """
        if as_of_date is None:
            as_of_date = date.today()

        signals_by_ticker: Dict[str, List[ParsedSignal]] = {}

        if not self.reports_dir.exists():
            return {}

        # 1. Scan individual report directories: reports/reports/<TICKER>_<YYYYMMDD>_<HHMMSS>/
        for folder in self.reports_dir.rglob("*"):
            if not folder.is_dir():
                continue

            match = self.FOLDER_PATTERN.match(folder.name)
            if not match:
                continue

            ticker, date_str, time_str = match.groups()
            try:
                report_dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
                report_date = report_dt.date()
            except ValueError:
                continue

            decision_file = folder / "5_portfolio" / "decision.md"
            complete_file = folder / "complete_report.md"

            target_file = decision_file if decision_file.exists() else (complete_file if complete_file.exists() else None)
            if not target_file:
                continue

            content = target_file.read_text(encoding="utf-8", errors="ignore")
            rating_match = self.RATING_PATTERN.search(content)
            if not rating_match:
                continue

            raw_rating = rating_match.group(1).strip()
            signal_type = SignalType.from_str(raw_rating)

            target_price = None
            tp_match = self.TARGET_PATTERN.search(content)
            if tp_match:
                try:
                    target_price = float(tp_match.group(1).replace(",", ""))
                except ValueError:
                    pass

            stop_loss = None
            sl_match = self.STOP_PATTERN.search(content)
            if sl_match:
                try:
                    stop_loss = float(sl_match.group(1).replace(",", ""))
                except ValueError:
                    pass

            age_days = (as_of_date - report_date).days
            is_expired = self.max_age_days > 0 and age_days > self.max_age_days
            days_remaining = max(0, self.max_age_days - age_days) if self.max_age_days > 0 else 999
            is_approaching_stale = not is_expired and days_remaining <= self.stale_warning_days

            parsed = ParsedSignal(
                ticker=ticker,
                signal=signal_type,
                date=report_date,
                source_path=str(target_file),
                target_price=target_price,
                stop_loss=stop_loss,
                age_days=age_days,
                days_remaining=days_remaining,
                is_approaching_stale=is_approaching_stale,
                is_expired=is_expired,
                raw_rating=raw_rating,
            )

            if ticker not in signals_by_ticker:
                signals_by_ticker[ticker] = []
            signals_by_ticker[ticker].append(parsed)

        # 2. Also inspect 00_Portfolio_Actions_Dashboard.md for any table ratings
        dashboard_file = self.reports_dir / "00_Portfolio_Actions_Dashboard.md"
        if dashboard_file.exists():
            self._parse_dashboard_table(dashboard_file, signals_by_ticker, as_of_date)

        # Deduplicate: Select strictly the newest report for each ticker
        final_signals: Dict[str, ParsedSignal] = {}
        for ticker, candidates in signals_by_ticker.items():
            candidates.sort(key=lambda x: (x.date, not x.is_expired), reverse=True)
            newest = candidates[0]
            final_signals[ticker] = newest

        return final_signals

    def _parse_dashboard_table(
        self,
        dashboard_path: Path,
        signals_by_ticker: Dict[str, List[ParsedSignal]],
        as_of_date: date,
    ):
        table_row_pattern = re.compile(
            r"\|\s*\*{0,2}\[?\[?([A-Z0-9]+)\]?\]?\*{0,2}\s*\|\s*\*{0,2}(\d{4}-\d{2}-\d{2})\*{0,2}\s*\|\s*\*{0,2}([A-Za-z\s→]+?)(?:[🟢🟡🔴🎯]|\*{0,2})\s*\|"
        )

        content = dashboard_path.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            m = table_row_pattern.search(line)
            if not m:
                continue

            ticker, date_str, raw_rating = m.groups()
            ticker = ticker.strip()
            raw_rating = raw_rating.strip()

            if ticker in signals_by_ticker and len(signals_by_ticker[ticker]) > 0:
                continue

            try:
                report_date = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
            except ValueError:
                continue

            signal_type = SignalType.from_str(raw_rating)
            age_days = (as_of_date - report_date).days
            is_expired = self.max_age_days > 0 and age_days > self.max_age_days
            days_remaining = max(0, self.max_age_days - age_days) if self.max_age_days > 0 else 999
            is_approaching_stale = not is_expired and days_remaining <= self.stale_warning_days

            parsed = ParsedSignal(
                ticker=ticker,
                signal=signal_type,
                date=report_date,
                source_path=str(dashboard_path),
                age_days=age_days,
                days_remaining=days_remaining,
                is_approaching_stale=is_approaching_stale,
                is_expired=is_expired,
                raw_rating=raw_rating,
            )

            if ticker not in signals_by_ticker:
                signals_by_ticker[ticker] = []
            signals_by_ticker[ticker].append(parsed)
