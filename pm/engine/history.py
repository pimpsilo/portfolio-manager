import csv
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

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
    source: str  # "VAULT_REPORT", "BROKER_CSV", or "LEDGER"


class TransactionHistoryTracker:
    """
    Tracks historical transactions across both generated Obsidian Trade Order reports
    and historical broker CSV exports to enforce transaction cadence and cooldowns.
    Maintains a persistent JSON ledger file (trade_history.json) so transaction history
    remains completely preserved even when ~/Downloads CSV files are deleted.
    """

    def __init__(
        self,
        obsidian_vault_dir: Optional[Union[str, Path]] = None,
        downloads_dir: Optional[Union[str, Path]] = None,
        history_file: Optional[Union[str, Path]] = None,
        lookback_days: int = 30,
        equivalent_symbols: Optional[List[List[str]]] = None,
    ):
        self.obsidian_vault_dir = Path(obsidian_vault_dir) if obsidian_vault_dir else None
        self.downloads_dir = Path(downloads_dir) if downloads_dir else None
        self.history_file = Path(history_file) if history_file else None
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

    def _empty_ledger(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "last_updated": None,
            "last_snapshot_date": None,
            "last_source_file": None,
            "last_holdings": {},
            "transactions": {},
        }

    def load_ledger(self) -> Dict[str, Any]:
        """Loads persistent trade history ledger from JSON file."""
        if not self.history_file or not self.history_file.exists():
            return self._empty_ledger()
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return self._empty_ledger()
                if "transactions" not in data:
                    data["transactions"] = {}
                if "last_holdings" not in data:
                    data["last_holdings"] = {}
                return data
        except Exception as e:
            logger.warning(f"Error loading trade history ledger from {self.history_file}: {e}")
            return self._empty_ledger()

    def save_ledger(self, data: Dict[str, Any]) -> None:
        """Saves trade history ledger atomically."""
        if not self.history_file:
            return
        try:
            target = self.history_file.resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_path = target.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            temp_path.replace(target)
        except Exception as e:
            logger.warning(f"Failed to save trade history ledger to {self.history_file}: {e}")

    def record_holdings_snapshot(
        self,
        holdings: Dict[str, float],
        snapshot_date: date,
        source_file: Optional[str] = None,
    ) -> List[TransactionRecord]:
        """
        Compares incoming holdings against last_holdings in the persistent ledger.
        If any quantity changed, logs a new confirmed broker transaction.
        Saves updated holdings snapshot to the ledger.
        """
        if not self.history_file:
            return []
        ledger = self.load_ledger()
        prev_holdings: Dict[str, float] = ledger.get("last_holdings", {})
        new_transactions: List[TransactionRecord] = []

        if prev_holdings:
            all_syms = set(prev_holdings.keys()) | set(holdings.keys())
            for sym in all_syms:
                q_prev = float(prev_holdings.get(sym, 0.0))
                q_curr = float(holdings.get(sym, 0.0))
                delta = q_curr - q_prev
                if abs(delta) >= 0.0001:
                    action = "BUY" if delta > 0 else "SELL"
                    tx_record = TransactionRecord(
                        ticker=sym,
                        trade_date=snapshot_date,
                        action=action,
                        shares=round(abs(delta), 4),
                        source="BROKER_CSV",
                    )
                    new_transactions.append(tx_record)

                    tx_list = ledger["transactions"].setdefault(sym, [])
                    already_exists = any(
                        t.get("trade_date") == snapshot_date.isoformat()
                        and t.get("action") == action
                        and abs(float(t.get("shares", 0)) - abs(delta)) < 0.0001
                        for t in tx_list
                    )
                    if not already_exists:
                        tx_list.append({
                            "trade_date": snapshot_date.isoformat(),
                            "action": action,
                            "shares": round(abs(delta), 4),
                            "source": "BROKER_CSV",
                        })

        ledger["last_holdings"] = {s: float(q) for s, q in holdings.items()}
        ledger["last_snapshot_date"] = snapshot_date.isoformat()
        ledger["last_source_file"] = source_file
        ledger["last_updated"] = datetime.now().isoformat()
        self.save_ledger(ledger)
        return new_transactions

    def record_trade_directives(
        self,
        allocations: List[Any],
        exec_date: date,
    ) -> None:
        """
        Records active BUY/SELL directives into the persistent ledger when orders are executed.
        """
        if not self.history_file:
            return
        ledger = self.load_ledger()
        changed = False

        for a in allocations:
            action = getattr(a, "action", "")
            shares = float(getattr(a, "order_shares", 0.0))
            ticker = getattr(a, "ticker", "")
            if action in ("BUY", "SELL") and shares > 0 and ticker:
                tx_list = ledger["transactions"].setdefault(ticker, [])
                already_exists = any(
                    t.get("trade_date") == exec_date.isoformat()
                    and t.get("action") == action
                    for t in tx_list
                )
                if not already_exists:
                    tx_list.append({
                        "trade_date": exec_date.isoformat(),
                        "action": action,
                        "shares": round(shares, 4),
                        "source": "VAULT_REPORT",
                    })
                    changed = True

        if changed:
            ledger["last_updated"] = datetime.now().isoformat()
            self.save_ledger(ledger)

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

                    cleaned_ticker_cell = ticker_cell.replace("*", "").strip()
                    tm = _TICKER_EXTRACT_RE.search(cleaned_ticker_cell)
                    if not tm:
                        continue
                    ticker = tm.group(1).upper()

                    action = "BUY" if "BUY" in action_cell else ("SELL" if "SELL" in action_cell else None)
                    if not action:
                        continue

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

        snapshots.sort(key=lambda s: (s[0], s[1]))

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
        Merges persistent ledger records, vault reports, and broker CSV quantity changes
        into a unified mapping of ticker -> most recent TransactionRecord, with dual-class symbol aliasing.
        """
        target_date = as_of_date or date.today()
        merged: Dict[str, TransactionRecord] = {}

        # 1. Load from persistent ledger file
        ledger = self.load_ledger()
        for ticker, tx_list in ledger.get("transactions", {}).items():
            if isinstance(tx_list, list):
                for item in tx_list:
                    try:
                        t_date = datetime.strptime(item["trade_date"], "%Y-%m-%d").date()
                    except (KeyError, ValueError):
                        continue
                    if t_date <= target_date:
                        existing = merged.get(ticker)
                        if existing is None or t_date > existing.trade_date:
                            merged[ticker] = TransactionRecord(
                                ticker=ticker,
                                trade_date=t_date,
                                action=item.get("action", "BUY"),
                                shares=float(item.get("shares", 0.0)),
                                source=item.get("source", "LEDGER"),
                            )

        # 2. Merge with vault reports on disk
        vault_records = self.scan_vault_reports(
            target_date,
            include_as_of_date=include_as_of_date_vault_orders,
        )
        for ticker, rec in vault_records.items():
            existing = merged.get(ticker)
            if existing is None or rec.trade_date >= existing.trade_date:
                merged[ticker] = rec

        # 3. Merge with broker CSV files in downloads_dir (if any exist)
        csv_records = self.scan_broker_csv_quantity_changes(target_date)
        for ticker, rec in csv_records.items():
            existing = merged.get(ticker)
            if existing is None or rec.trade_date >= existing.trade_date:
                merged[ticker] = rec

        # 4. If ledger had empty transactions, auto-bootstrap with discovered transactions
        if not ledger.get("transactions") and merged:
            for ticker, rec in merged.items():
                ledger["transactions"].setdefault(ticker, []).append({
                    "trade_date": rec.trade_date.isoformat(),
                    "action": rec.action,
                    "shares": rec.shares,
                    "source": rec.source,
                })
            ledger["last_updated"] = datetime.now().isoformat()
            self.save_ledger(ledger)

        # 5. Apply dual-class symbol aliasing
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
