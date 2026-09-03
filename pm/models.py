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
    is_expired: bool = False
    raw_rating: str = ""


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
    execution_date: date = field(default_factory=date.today)
