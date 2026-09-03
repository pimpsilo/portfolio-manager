import csv
import re
from pathlib import Path
from typing import Dict, List, Optional
from pm.models import Holding, PortfolioState


class BrokerCSVParser:
    """
    Parses and cleans Fidelity Portfolio_Positions_*.csv export files.
    """

    CASH_SYMBOLS = {"FDRXX", "SPAXX", "FCASH"}

    def __init__(self, downloads_dir: Optional[str] = None):
        self.downloads_dir = Path(downloads_dir) if downloads_dir else Path.home() / "Downloads"

    def find_latest_csv(self) -> Optional[Path]:
        """
        Locates the most recently modified Portfolio_Positions_*.csv file.
        """
        if not self.downloads_dir.exists():
            return None

        candidates = list(self.downloads_dir.glob("Portfolio_Positions_*.csv"))
        if not candidates:
            return None

        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    @staticmethod
    def _clean_float(val: str) -> float:
        if not val:
            return 0.0
        cleaned = val.replace("$", "").replace(",", "").replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @staticmethod
    def _clean_symbol(sym: str) -> str:
        if not sym:
            return ""
        return re.sub(r"\*+", "", sym).strip().upper()

    def parse(self, csv_path: Optional[str] = None) -> PortfolioState:
        """
        Parses a Fidelity CSV file into a clean PortfolioState.
        """
        target_path: Optional[Path] = Path(csv_path) if csv_path else self.find_latest_csv()

        if not target_path or not target_path.exists():
            raise FileNotFoundError(f"Could not locate broker CSV file at {target_path or self.downloads_dir}")

        cash_balance = 0.0
        holdings: Dict[str, Holding] = {}
        as_of_date: Optional[str] = None

        with open(target_path, "r", encoding="utf-8-sig", errors="ignore") as f:
            reader = csv.reader(f)
            headers: Optional[List[str]] = None

            for row in reader:
                if not row:
                    continue

                # Header detection
                if not headers and any("Symbol" in cell for cell in row):
                    headers = [c.strip() for c in row]
                    continue

                if not headers:
                    continue

                row_dict = {headers[i]: row[i].strip() for i in range(min(len(headers), len(row)))}

                raw_symbol = row_dict.get("Symbol", "")
                description = row_dict.get("Description", "")
                current_val_str = row_dict.get("Current value", "")

                # Check for footer date
                first_cell = row[0] if len(row) > 0 else ""
                if "Date downloaded" in first_cell:
                    as_of_date = first_cell.strip('"')
                    continue

                # Ignore disclaimers or summary rows
                if "The data and information in this spreadsheet" in first_cell or "Brokerage services are provided" in first_cell:
                    continue
                if not raw_symbol and not description:
                    continue

                clean_sym = self._clean_symbol(raw_symbol)

                # Cash detection: FDRXX, SPAXX, or "HELD IN MONEY MARKET"
                if clean_sym in self.CASH_SYMBOLS or "HELD IN MONEY MARKET" in description.upper():
                    cash_val = self._clean_float(current_val_str)
                    cash_balance += cash_val
                    continue

                # Filter uninvestable rows
                if not clean_sym or clean_sym in ("PENDING ACTIVITY", "ACCOUNT TOTAL"):
                    continue

                qty = self._clean_float(row_dict.get("Quantity", "0"))
                last_price = self._clean_float(row_dict.get("Last price", "0"))
                current_val = self._clean_float(current_val_str)
                cost_basis = self._clean_float(row_dict.get("Cost basis total", "0"))
                unrealized_gain = self._clean_float(row_dict.get("Total gain/loss dollar", "0"))

                if qty <= 0 and current_val <= 0:
                    continue

                holdings[clean_sym] = Holding(
                    symbol=clean_sym,
                    description=description,
                    quantity=qty,
                    last_price=last_price,
                    current_value=current_val,
                    cost_basis=cost_basis,
                    unrealized_gain_loss=unrealized_gain,
                )

        equity_value = sum(h.current_value for h in holdings.values())
        total_value = equity_value + cash_balance
        cash_pct = (cash_balance / total_value * 100.0) if total_value > 0 else 0.0

        return PortfolioState(
            total_account_value=total_value,
            cash_balance=cash_balance,
            cash_percent=cash_pct,
            holdings=holdings,
            as_of_date=as_of_date,
        )
