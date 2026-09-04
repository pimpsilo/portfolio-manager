from datetime import date
from pm.engine.controller import PortfolioManagerEngine
from pm.models import Holding, ParsedSignal, PortfolioState, SignalType


def test_goog_googl_equivalence_deduplication():
    # Setup engine with GOOG and GOOGL equivalence
    engine = PortfolioManagerEngine()

    # Mock portfolio holding 50 shares of GOOG (Class C)
    engine.csv_parser.parse = lambda *args, **kwargs: PortfolioState(
        total_account_value=100000.0,
        cash_balance=20000.0,
        cash_percent=0.20,
        holdings={
            "GOOG": Holding("GOOG", "Alphabet Class C", quantity=50, last_price=300.0, current_value=15000.0)
        }
    )

    # Mock signals: GOOG has Hold, but GOOGL has Overweight
    engine.signal_parser.parse_all_signals = lambda *args, **kwargs: {
        "GOOG": ParsedSignal("GOOG", SignalType.EQUAL_WEIGHT, date(2026, 8, 31), ""),
        "GOOGL": ParsedSignal("GOOGL", SignalType.OVERWEIGHT, date(2026, 9, 1), ""),
    }

    # Run solver
    summary = engine.run_solver()

    universe_tickers = [a.ticker for a in summary.allocations]

    # 1. GOOG must be in universe because it is held
    assert "GOOG" in universe_tickers

    # 2. GOOGL must NOT be in universe (must not buy duplicate Alphabet shares!)
    assert "GOOGL" not in universe_tickers

    # 3. GOOG must have adopted the OVERWEIGHT signal from GOOGL
    goog_alloc = next(a for a in summary.allocations if a.ticker == "GOOG")
    assert goog_alloc.signal == SignalType.OVERWEIGHT
