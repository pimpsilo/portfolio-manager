from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Optional


class SignalType(str, Enum):
    OVERWEIGHT = "OVERWEIGHT"
    EQUAL_WEIGHT = "EQUAL_WEIGHT"
    UNDERWEIGHT = "UNDERWEIGHT"
    AVOID = "AVOID"

    @classmethod
    def from_str(cls, val: str) -> "SignalType":
        clean = val.strip().upper()
        if clean in ("OVERWEIGHT", "BUY", "STRONG BUY"):
            return cls.OVERWEIGHT
        elif clean in ("HOLD", "EQUAL_WEIGHT", "EQUAL-WEIGHT", "NEUTRAL"):
            return cls.EQUAL_WEIGHT
        elif clean in ("UNDERWEIGHT", "REDUCE", "TRIM"):
            return cls.UNDERWEIGHT
        elif clean in ("AVOID", "SELL", "STRONG SELL"):
            return cls.AVOID
        return cls.EQUAL_WEIGHT


@dataclass
class ParsedSignal:
    ticker: str
    signal: SignalType
    date: date
    source_path: str
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    age_days: int = 0
    days_remaining: int = 14
    is_approaching_stale: bool = False
    is_expired: bool = False
    raw_rating: str = ""
    in_portfolio: bool = False


@dataclass
class Holding:
    symbol: str
    description: str
    quantity: float
    last_price: float
    current_value: float
    cost_basis: float = 0.0
    unrealized_gain_loss: float = 0.0


@dataclass
class PortfolioState:
    total_account_value: float
    cash_balance: float
    cash_percent: float
    holdings: Dict[str, Holding]
    as_of_date: Optional[str] = None


@dataclass
class AllocationResult:
    ticker: str
    current_shares: float
    realtime_price: float
    current_value: float
    current_weight: float
    signal: SignalType
    cluster_id: int
    base_weight: float
    target_weight: float
    target_value: float
    dollar_delta: float
    drift_pct: float
    action: str  # "BUY", "SELL", "HOLD"
    order_shares: float
    is_whole_share: bool
    reason: str
    report_path: Optional[str] = None
    report_date: Optional[date] = None


@dataclass
class ReconciliationSummary:
    total_portfolio_value: float
    current_cash: float
    target_cash_reserve: float
    projected_ending_cash: float
    total_buys_dollars: float
    total_sells_dollars: float
    allocations: List[AllocationResult]
    clusters: Dict[int, List[str]]
    aging_signals: List[ParsedSignal] = field(default_factory=list)
    expired_signals: List[ParsedSignal] = field(default_factory=list)
    max_age_days: int = 14
    execution_date: date = field(default_factory=date.today)


@dataclass
class TriageItem:
    ticker: str
    category: str
    asset_type: str
    analysts: List[str]
    in_portfolio: bool
    status: str  # "MISSING", "EXPIRED", "STALE_SOON", "FRESH"
    priority: int  # 1 (highest) to 5 (fresh/skip)
    age_days: Optional[int] = None
    days_remaining: Optional[int] = None
    current_signal: Optional[SignalType] = None
    reason: str = ""


@dataclass
class TriagePlan:
    items: List[TriageItem]
    execution_date: date = field(default_factory=date.today)

    @property
    def queue(self) -> List[TriageItem]:
        """Returns items needing agent evaluation, sorted by priority (ascending: 1, 2, ...)."""
        return sorted([it for it in self.items if it.priority < 5], key=lambda x: (x.priority, x.ticker))

    @property
    def fresh_items(self) -> List[TriageItem]:
        return [it for it in self.items if it.priority >= 5]

    @property
    def summary_counts(self) -> Dict[str, int]:
        counts = {"MISSING": 0, "EXPIRED": 0, "STALE_SOON": 0, "FRESH": 0}
        for it in self.items:
            counts[it.status] = counts.get(it.status, 0) + 1
        return counts

    def get_queue(self, limit: Optional[int] = None) -> List[TriageItem]:
        q = self.queue
        if limit is not None and limit > 0:
            return q[:limit]
        return q

