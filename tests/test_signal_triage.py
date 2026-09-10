from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from pm.agents.triage import SignalTriageEngine
from pm.models import Holding, ParsedSignal, PortfolioState, SignalType, TriageItem, TriagePlan


@pytest.fixture
def mock_config():
    return {
        "paths": {
            "reports_dir": "/mock/reports",
            "downloads_dir": "/mock/downloads",
        },
        "signals": {
            "max_age_days": 14,
            "stale_warning_days": 4,
        },
        "equivalent_symbols": [
            ["GOOG", "GOOGL"],
        ],
        "watchlist": {
            "categories": {
                "equities": {
                    "asset_type": "stock",
                    "analysts": ["market", "news", "fundamentals"],
                    "tickers": ["AAPL", "MSFT", "GOOGL", "NVDA"],
                },
                "etfs_index": {
                    "asset_type": "etf",
                    "analysts": ["market", "news"],
                    "tickers": ["VOO", "QQQ"],
                },
            },
        },
    }


def test_triage_classification(mock_config):
    today = date(2026, 9, 7)

    # Mock portfolio holdings: AAPL (held), GOOGL (held)
    mock_holdings = {
        "AAPL": Holding(symbol="AAPL", description="Apple", quantity=10, last_price=200.0, current_value=2000.0),
        "GOOGL": Holding(symbol="GOOGL", description="Google", quantity=5, last_price=180.0, current_value=900.0),
    }
    mock_portfolio = PortfolioState(
        total_account_value=10000.0,
        cash_balance=7100.0,
        cash_percent=71.0,
        holdings=mock_holdings,
    )

    # Mock signals:
    # - AAPL: Held, but report is 12 days old -> STALE_SOON (<= 4d left)
    # - GOOGL: Held, fresh (2 days old) -> FRESH
    # - MSFT: Candidate, missing
    # - NVDA: Candidate, expired (16 days old) -> EXPIRED
    # - VOO: Candidate, fresh (1 day old) -> FRESH
    # - QQQ: Candidate, approaching stale (11 days old, 3d left) -> STALE_SOON
    mock_signals = {
        "AAPL": ParsedSignal(
            ticker="AAPL",
            signal=SignalType.OVERWEIGHT,
            date=today - timedelta(days=12),
            source_path="/mock/reports/AAPL_report",
            age_days=12,
            days_remaining=2,
            is_approaching_stale=True,
            is_expired=False,
        ),
        "GOOGL": ParsedSignal(
            ticker="GOOGL",
            signal=SignalType.OVERWEIGHT,
            date=today - timedelta(days=2),
            source_path="/mock/reports/GOOGL_report",
            age_days=2,
            days_remaining=12,
            is_approaching_stale=False,
            is_expired=False,
        ),
        "NVDA": ParsedSignal(
            ticker="NVDA",
            signal=SignalType.OVERWEIGHT,
            date=today - timedelta(days=16),
            source_path="/mock/reports/NVDA_report",
            age_days=16,
            days_remaining=0,
            is_approaching_stale=False,
            is_expired=True,
        ),
        "VOO": ParsedSignal(
            ticker="VOO",
            signal=SignalType.EQUAL_WEIGHT,
            date=today - timedelta(days=1),
            source_path="/mock/reports/VOO_report",
            age_days=1,
            days_remaining=13,
            is_approaching_stale=False,
            is_expired=False,
        ),
        "QQQ": ParsedSignal(
            ticker="QQQ",
            signal=SignalType.EQUAL_WEIGHT,
            date=today - timedelta(days=11),
            source_path="/mock/reports/QQQ_report",
            age_days=11,
            days_remaining=3,
            is_approaching_stale=True,
            is_expired=False,
        ),
    }

    triage_engine = SignalTriageEngine(mock_config)

    with patch.object(triage_engine.csv_parser, "parse", return_value=mock_portfolio), \
         patch.object(triage_engine.signal_parser, "parse_all_signals", return_value=mock_signals):

        plan = triage_engine.run_triage(as_of_date=today)

    # Verify counts
    assert plan.summary_counts["FRESH"] == 2  # GOOGL, VOO
    assert plan.summary_counts["STALE_SOON"] == 2  # AAPL, QQQ
    assert plan.summary_counts["MISSING"] == 1  # MSFT
    assert plan.summary_counts["EXPIRED"] == 1  # NVDA

    items_by_ticker = {it.ticker: it for it in plan.items}

    # AAPL: Held, Stale Soon -> Priority 1
    assert items_by_ticker["AAPL"].priority == 1
    assert items_by_ticker["AAPL"].status == "STALE_SOON"
    assert items_by_ticker["AAPL"].in_portfolio is True

    # MSFT: Candidate, Missing -> Priority 2
    assert items_by_ticker["MSFT"].priority == 2
    assert items_by_ticker["MSFT"].status == "MISSING"
    assert items_by_ticker["MSFT"].in_portfolio is False

    # QQQ: Candidate, Stale Soon -> Priority 3
    assert items_by_ticker["QQQ"].priority == 3
    assert items_by_ticker["QQQ"].status == "STALE_SOON"

    # NVDA: Candidate, Expired -> Priority 4
    assert items_by_ticker["NVDA"].priority == 4
    assert items_by_ticker["NVDA"].status == "EXPIRED"

    # GOOGL: Fresh -> Priority 5
    assert items_by_ticker["GOOGL"].priority == 5
    assert items_by_ticker["GOOGL"].status == "FRESH"

    # VOO: Fresh -> Priority 5
    assert items_by_ticker["VOO"].priority == 5
    assert items_by_ticker["VOO"].status == "FRESH"

    # Check Queue ordering (Priority 1 -> 2 -> 3 -> 4)
    queue = plan.queue
    assert [q.ticker for q in queue] == ["AAPL", "MSFT", "QQQ", "NVDA"]

    # Check slicing
    sliced = plan.get_queue(limit=2)
    assert len(sliced) == 2
    assert sliced[0].ticker == "AAPL"
    assert sliced[1].ticker == "MSFT"

    # Check table formatting output
    table_str = SignalTriageEngine.format_triage_table(plan)
    assert "ACTIONABLE RESEARCH QUEUE" in table_str
    assert "AAPL" in table_str
    assert "FRESH REPORTS ON FILE" in table_str
    assert "VOO" in table_str


def test_triage_with_extra_tickers(mock_config):
    triage_engine = SignalTriageEngine(mock_config)

    mock_portfolio = PortfolioState(
        total_account_value=10000.0,
        cash_balance=10000.0,
        cash_percent=100.0,
        holdings={},
    )

    with patch.object(triage_engine.csv_parser, "parse", return_value=mock_portfolio), \
         patch.object(triage_engine.signal_parser, "parse_all_signals", return_value={}):

        plan = triage_engine.run_triage(extra_tickers=["TSLA", "COIN"])

    tickers = {it.ticker for it in plan.items}
    assert "TSLA" in tickers
    assert "COIN" in tickers
    assert {it.ticker: it.category for it in plan.items}["TSLA"] == "manual_request"


def test_triage_ingest_stocks_file(tmp_path):
    stocks_file = tmp_path / "custom_stocks.csv"
    stocks_file.write_text("# Tech and retail targets\nAAPL\nMSFT\n\n# Consumer\nCOST\n", encoding="utf-8")

    cfg = {
        "paths": {
            "reports_dir": str(tmp_path / "reports"),
            "downloads_dir": str(tmp_path / "downloads"),
        },
        "watchlist": {
            "stocks_file": str(stocks_file),
            "categories": {
                "bonds": {
                    "asset_type": "bond",
                    "analysts": ["market", "news"],
                    "tickers": ["BND"],
                },
            },
        },
    }

    triage_engine = SignalTriageEngine(cfg)

    mock_portfolio = PortfolioState(
        total_account_value=10000.0,
        cash_balance=10000.0,
        cash_percent=100.0,
        holdings={},
    )

    with patch.object(triage_engine.csv_parser, "parse", return_value=mock_portfolio), \
         patch.object(triage_engine.signal_parser, "parse_all_signals", return_value={}):
        plan = triage_engine.run_triage()

    items_by_ticker = {it.ticker: it for it in plan.items}
    assert "AAPL" in items_by_ticker
    assert "MSFT" in items_by_ticker
    assert "COST" in items_by_ticker
    assert "BND" in items_by_ticker

    assert items_by_ticker["AAPL"].category == "stocks_watchlist"
    assert items_by_ticker["AAPL"].asset_type == "stock"
    assert items_by_ticker["BND"].category == "bonds"
    assert items_by_ticker["BND"].asset_type == "bond"


def test_triage_min_age_threshold(mock_config):
    today = date(2026, 9, 7)
    triage_engine = SignalTriageEngine(mock_config)

    mock_holdings = {
        "AAPL": Holding(symbol="AAPL", description="Apple", quantity=10, last_price=200.0, current_value=2000.0),
    }
    mock_portfolio = PortfolioState(
        total_account_value=10000.0,
        cash_balance=8000.0,
        cash_percent=80.0,
        holdings=mock_holdings,
    )

    # AAPL (held, 4 days old), GOOGL (candidate, 2 days old), MSFT (candidate, 5 days old), NVDA (candidate, missing)
    mock_signals = {
        "AAPL": ParsedSignal(
            ticker="AAPL", signal=SignalType.OVERWEIGHT, date=today - timedelta(days=4),
            source_path="/mock", age_days=4, days_remaining=10, is_approaching_stale=False, is_expired=False,
        ),
        "GOOGL": ParsedSignal(
            ticker="GOOGL", signal=SignalType.OVERWEIGHT, date=today - timedelta(days=2),
            source_path="/mock", age_days=2, days_remaining=12, is_approaching_stale=False, is_expired=False,
        ),
        "MSFT": ParsedSignal(
            ticker="MSFT", signal=SignalType.EQUAL_WEIGHT, date=today - timedelta(days=5),
            source_path="/mock", age_days=5, days_remaining=9, is_approaching_stale=False, is_expired=False,
        ),
    }

    with patch.object(triage_engine.csv_parser, "parse", return_value=mock_portfolio), \
         patch.object(triage_engine.signal_parser, "parse_all_signals", return_value=mock_signals):

        # 1. min_age_days = 3:
        # AAPL (4d >= 3) -> Queued (Priority 1)
        # MSFT (5d >= 3) -> Queued (Priority 3)
        # NVDA (missing) -> Queued (Priority 2)
        # GOOGL (2d < 3) -> FRESH (Priority 5)
        plan_3d = triage_engine.run_triage(as_of_date=today, min_age_days=3)
        queue_tickers = [q.ticker for q in plan_3d.queue]
        assert "AAPL" in queue_tickers
        assert "MSFT" in queue_tickers
        assert "NVDA" in queue_tickers
        assert "GOOGL" not in queue_tickers

        # 2. min_age_days = 0 (--all / refresh all):
        # Everything with an existing report should be queued
        plan_all = triage_engine.run_triage(as_of_date=today, min_age_days=0)
        all_queue_tickers = [q.ticker for q in plan_all.queue]
        assert "AAPL" in all_queue_tickers
        assert "MSFT" in all_queue_tickers
        assert "NVDA" in all_queue_tickers
        assert "GOOGL" in all_queue_tickers


def test_triage_category_filter(mock_config):
    today = date(2026, 9, 7)
    triage_engine = SignalTriageEngine(mock_config)

    mock_portfolio = PortfolioState(
        total_account_value=10000.0, cash_balance=10000.0, cash_percent=100.0, holdings={},
    )

    with patch.object(triage_engine.csv_parser, "parse", return_value=mock_portfolio), \
         patch.object(triage_engine.signal_parser, "parse_all_signals", return_value={}):

        plan = triage_engine.run_triage(as_of_date=today, category="etfs_index")

    categories = {it.category for it in plan.items}
    assert categories == {"etfs_index"}
    assert {it.ticker for it in plan.items} == {"VOO", "QQQ"}


def test_triage_excluded_tickers(mock_config):
    today = date(2026, 9, 7)
    mock_config["signals"]["excluded_tickers"] = ["VOO", "QQQ"]
    triage_engine = SignalTriageEngine(mock_config)

    mock_portfolio = PortfolioState(
        total_account_value=10000.0, cash_balance=10000.0, cash_percent=100.0, holdings={},
    )

    with patch.object(triage_engine.csv_parser, "parse", return_value=mock_portfolio), \
         patch.object(triage_engine.signal_parser, "parse_all_signals", return_value={}):
        plan = triage_engine.run_triage(as_of_date=today)

    tickers = {it.ticker for it in plan.items}
    assert "VOO" not in tickers
    assert "QQQ" not in tickers
    assert "AAPL" in tickers


def test_triage_empty_categories_stocks_only(tmp_path):
    stocks_file = tmp_path / "my_stocks.csv"
    stocks_file.write_text("AAPL\nMSFT\nNVDA\n", encoding="utf-8")

    cfg = {
        "paths": {
            "reports_dir": str(tmp_path / "reports"),
            "downloads_dir": str(tmp_path / "downloads"),
        },
        "signals": {
            "max_age_days": 14,
            "stale_warning_days": 4,
            "excluded_tickers": [],
        },
        "watchlist": {
            "stocks_file": str(stocks_file),
            "categories": {},
        },
    }

    triage_engine = SignalTriageEngine(cfg)
    mock_portfolio = PortfolioState(
        total_account_value=10000.0, cash_balance=10000.0, cash_percent=100.0, holdings={},
    )

    with patch.object(triage_engine.csv_parser, "parse", return_value=mock_portfolio), \
         patch.object(triage_engine.signal_parser, "parse_all_signals", return_value={}):
        plan = triage_engine.run_triage()

    assert {it.ticker for it in plan.items} == {"AAPL", "MSFT", "NVDA"}
    for it in plan.items:
        assert it.category == "stocks_watchlist"
        assert it.asset_type == "stock"


