import csv
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

from pm.ingest.broker_csv import BrokerCSVParser

logger = logging.getLogger(__name__)

_TRADE_ORDERS_FILE_RE = re.compile(r"^Trade_Orders_(\d{4}-\d{2}-\d{2})\.md$")
_TICKER_EXTRACT_RE = re.compile(r"\[?([A-Z0-9.\-]+)\]?")


@dataclass
class TransactionRecord:
    ticker: str
    trade_date: date
    action: str  # "BUY" or "SELL"
    shares: float
    source: str  # "VAULT_REPORT" or "BROKER_CSV"


class TransactionHistoryTracker:
    """
    Tracks historical transactions across both generated Obsidian Trade Order reports
    and historical broker CSV exports to enforce transaction cadence and cooldowns.
    """

    def __init__(
        self,
        obsidian_vault_dir: Optional[Union[str, Path]] = None,
        downloads_dir: Optional[Union[str, Path]] = None,
        lookback_days: int = 30,
        equivalent_symbols: Optional[List[List[str]]] = None,
    ):
        self.obsidian_vault_dir = Path(obsidian_vault_dir) if obsidian_vault_dir else None
        self.downloads_dir = Path(downloads_dir) if downloads_dir else None
        self.lookback_days = lookback_days
        self.equivalent_symbols = equivalent_symbols or [
            ["GOOG", "GOOGL"],
            ["FOX", "FOXA"],
            ["BRK.A", "BRK.B"],
        ]

    def _alias_map(self) -> Dict[str, Set[str]]:
        """Maps each symbol to all its equivalent share classes (including itself)."""
        mapping: Dict[str, Set[str]] = {}
        for group in self.equivalent_symbols:
            group_set = set(group)
            for sym in group:
                mapping[sym] = group_set
        return mapping

    def scan_vault_reports(
        self,
        as_of_date: date,
        include_as_of_date: bool = False,
    ) -> Dict[str, TransactionRecord]:
        """
        Parses past Trade_Orders_YYYY-MM-DD.md files in the Obsidian vault.
        Only considers files strictly before `as_of_date` by default, so a re-run
        on the current day is not self-suppressed by today's output file.
        """
        records: Dict[str, TransactionRecord] = {}
        if not self.obsidian_vault_dir or not self.obsidian_vault_dir.exists():
            return records

        min_date = as_of_date - timedelta(days=self.lookback_days)
        files = list(self.obsidian_vault_dir.glob("Trade_Orders_*.md"))

        for file_path in files:
            m = _TRADE_ORDERS_FILE_RE.match(file_path.name)
            if not m:
                continue

            try:
                file_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue

            if file_date < min_date:
                continue
            if not include_as_of_date and file_date >= as_of_date:
                continue
            if include_as_of_date and file_date > as_of_date:
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read vault report {file_path.name}: {e}")
                continue

            # Look for Immediate / Daily Actions section
            if "## 🎯 1. Immediate / Daily Actions" not in content:
                continue

            section = content.split("## 🎯 1. Immediate / Daily Actions")[1].split("## ")[0]
            for line in section.splitlines():
                line = line.strip()
                if not line.startswith("|") or "---" in line or "Directive Rationale" in line:
                    continue

                cols = [c.strip() for c in line.split("|")[1:-1]]
                if len(cols) >= 8:
                    ticker_cell = cols[0]
                    action_cell = cols[6].upper()
                    shares_cell = cols[7]

                    # Extract ticker
                    cleaned_ticker_cell = ticker_cell.replace("*", "").strip()
                    tm = _TICKER_EXTRACT_RE.search(cleaned_ticker_cell)
                    if not tm:
                        continue
                    ticker = tm.group(1).upper()

                    # Check action
                    action = "BUY" if "BUY" in action_cell else ("SELL" if "SELL" in action_cell else None)
                    if not action:
                        continue

                    # Extract shares
                    clean_shares = re.sub(r"[^0-9.]", "", shares_cell)
                    try:
                        order_shares = float(clean_shares)
                    except ValueError:
                        order_shares = 0.0

                    if order_shares <= 0:
                        continue

                    existing = records.get(ticker)
                    if existing is None or file_date > existing.trade_date:
                        records[ticker] = TransactionRecord(
                            ticker=ticker,
                            trade_date=file_date,
                            action=action,
                            shares=order_shares,
                            source="VAULT_REPORT",
                        )

        return records

    def scan_broker_csv_quantity_changes(
        self,
        as_of_date: date,
    ) -> Dict[str, TransactionRecord]:
        """
        Compares sequential Portfolio_Positions_*.csv files in downloads_dir to detect
        actual executed broker transactions where share quantity changed.
        """
        records: Dict[str, TransactionRecord] = {}
        if not self.downloads_dir or not self.downloads_dir.exists():
            return records

        min_date = as_of_date - timedelta(days=self.lookback_days)
        csv_files = list(self.downloads_dir.glob("Portfolio_Positions_*.csv"))
        if not csv_files:
            return records

        parser = BrokerCSVParser(downloads_dir=str(self.downloads_dir))
        snapshots: List[Tuple[date, float, Dict[str, float]]] = []

        for p in csv_files:
            try:
                state = parser.parse(str(p))
                csv_date: Optional[date] = None

                # Extract date from download_time or source_file
                for text_source in (state.download_time, state.source_file, p.name):
                    if text_source:
                        m = re.search(r"([A-Za-z]{3})-(\d{1,2})-(\d{4})", text_source)
                        if m:
                            try:
                                csv_date = datetime.strptime(
                                    f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "%b-%d-%Y"
                                ).date()
                                break
                            except ValueError:
                                pass

                if not csv_date:
                    try:
                        csv_date = datetime.fromtimestamp(p.stat().st_mtime).date()
                    except OSError:
                        continue

                if csv_date < min_date or csv_date > as_of_date:
                    continue

                mtime = p.stat().st_mtime if p.exists() else 0.0
                quantities = {s: h.quantity for s, h in state.holdings.items()}
                snapshots.append((csv_date, mtime, quantities))
            except Exception as e:
                logger.debug(f"Skipping CSV {p.name} during history scan: {e}")
                continue

        # Sort chronologically by (date, mtime)
        snapshots.sort(key=lambda s: (s[0], s[1]))

        # Compare consecutive snapshots
        for i in range(1, len(snapshots)):
            prev_date, _, prev_holdings = snapshots[i - 1]
            curr_date, _, curr_holdings = snapshots[i]

            all_symbols = set(prev_holdings.keys()) | set(curr_holdings.keys())
            for sym in all_symbols:
                q_prev = prev_holdings.get(sym, 0.0)
                q_curr = curr_holdings.get(sym, 0.0)
                delta = q_curr - q_prev

                if abs(delta) > 0.0001:
                    action = "BUY" if delta > 0 else "SELL"
                    existing = records.get(sym)
                    if existing is None or curr_date > existing.trade_date:
                        records[sym] = TransactionRecord(
                            ticker=sym,
                            trade_date=curr_date,
                            action=action,
                            shares=abs(delta),
                            source="BROKER_CSV",
                        )

        return records

    def get_last_transactions(
        self,
        as_of_date: Optional[date] = None,
        include_as_of_date_vault_orders: bool = False,
    ) -> Dict[str, TransactionRecord]:
        """
        Merges vault reports and broker CSV quantity changes into a unified mapping
        of ticker -> most recent TransactionRecord, with dual-class symbol aliasing.
        """
        target_date = as_of_date or date.today()
        vault_records = self.scan_vault_reports(
            target_date,
            include_as_of_date=include_as_of_date_vault_orders,
        )
        csv_records = self.scan_broker_csv_quantity_changes(target_date)

        merged: Dict[str, TransactionRecord] = {}
        all_tickers = set(vault_records.keys()) | set(csv_records.keys())

        for ticker in all_tickers:
            v_rec = vault_records.get(ticker)
            c_rec = csv_records.get(ticker)

            if v_rec and c_rec:
                # Take the more recent transaction
                chosen = c_rec if c_rec.trade_date >= v_rec.trade_date else v_rec
            elif c_rec:
                chosen = c_rec
            else:
                chosen = v_rec  # type: ignore

            merged[ticker] = chosen

        # Apply dual-class symbol aliasing
        alias_groups = self._alias_map()
        final_map: Dict[str, TransactionRecord] = dict(merged)

        for ticker, record in list(merged.items()):
            if ticker in alias_groups:
                for equivalent_ticker in alias_groups[ticker]:
                    existing = final_map.get(equivalent_ticker)
                    if existing is None or record.trade_date > existing.trade_date:
                        final_map[equivalent_ticker] = TransactionRecord(
                            ticker=equivalent_ticker,
                            trade_date=record.trade_date,
                            action=record.action,
                            shares=record.shares,
                            source=record.source,
                        )

        return final_map

    def get_cooldown_status(
        self,
        ticker: str,
        as_of_date: date,
        cooldown_days: int = 7,
        last_transactions: Optional[Dict[str, TransactionRecord]] = None,
    ) -> Optional[Tuple[date, int, int]]:
        """
        Returns (last_trade_date, days_since, days_remaining) if the ticker is under
        active cooldown on `as_of_date`, or None if eligible to trade.
        """
        if cooldown_days <= 0:
            return None

        if last_transactions is None:
            last_transactions = self.get_last_transactions(as_of_date=as_of_date)

        record = last_transactions.get(ticker)
        if not record:
            return None

        days_since = (as_of_date - record.trade_date).days
        if 0 <= days_since < cooldown_days:
            days_remaining = cooldown_days - days_since
            return (record.trade_date, days_since, days_remaining)

        return None
