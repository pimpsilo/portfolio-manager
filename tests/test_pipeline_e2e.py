from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from pm.agents.bridge import AgentEvaluationResult, TradingAgentsBridge
from pm.agents.triage import SignalTriageEngine
from pm.engine.controller import PortfolioManagerEngine
from pm.models import Holding, PortfolioState, SignalType


@pytest.fixture
def e2e_config(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    return {
        "paths": {
            "reports_dir": str(reports_dir),
            "downloads_dir": str(downloads_dir),
            "obsidian_vault_dir": str(reports_dir),
        },
        "signals": {
            "max_age_days": 14,
            "stale_warning_days": 4,
            "candidate_min_signal": "OVERWEIGHT",
        },
        "equivalent_symbols": [
            ["GOOG", "GOOGL"],
        ],
        "risk": {
            "max_position_weight": 0.15,
            "min_cash_reserve": 0.15,
            "enforce_cash_reserve_on_buys": True,
            "max_cluster_exposure": 0.25,
            "clustering_cophenetic_threshold": 0.5,
            "history_lookback_days": 180,
        },
        "market_cap_weighting": {
            "enabled": True,
            "mega_cap_threshold": 200000000000,
            "large_cap_threshold": 50000000000,
            "tier_multipliers": {
                "mega_cap": 3.0,
                "large_cap": 1.8,
                "mid_cap": 1.0,
            },
        },
        "multipliers": {
            "OVERWEIGHT": 1.5,
            "EQUAL_WEIGHT": 1.0,
            "UNDERWEIGHT": 0.5,
            "AVOID": 0.0,
        },
        "rebalance": {
            "drift_mode": "relative",
            "relative_threshold": 0.20,
            "hold_drift_tolerance": 1.00,
            "min_dollar_trade": 1500.0,
            "prefer_whole_shares": True,
            "liquidate_avoid": True,
            "stepping": {
                "enabled": True,
                "starter_step_factor": 0.50,
                "rebalance_step_factor": 0.50,
                "max_trade_dollar_cap": 6000.0,
                "avoid_step_factor": 1.00,
            },
        },
        "watchlist": {
            "auto_triage": True,
            "max_batch_size": 5,
            "rate_limit_delay_seconds": 0.0,
            "categories": {
                "equities": {
                    "asset_type": "stock",
                    "analysts": ["market", "news", "fundamentals"],
                    "tickers": ["AAPL", "NVDA"],
                },
            },
        },
        "tradingagents": {
            "llm_provider": "anthropic",
            "quick_think_llm": "claude-3-5-haiku-20241022",
            "deep_think_llm": "claude-3-5-sonnet-20241022",
            "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 1,
        },
    }


def test_pipeline_integration_flow(e2e_config, tmp_path):
    today = date(2026, 9, 7)
    reports_dir = Path(e2e_config["paths"]["reports_dir"])

    # 1. Start with portfolio holding AAPL and empty watchlist candidate NVDA
    mock_holdings = {
        "AAPL": Holding(symbol="AAPL", description="Apple", quantity=20, last_price=150.0, current_value=3000.0),
    }
    mock_portfolio = PortfolioState(
        total_account_value=30000.0,
        cash_balance=27000.0,
        cash_percent=90.0,
        holdings=mock_holdings,
    )

    # 2. Run Triage: AAPL has no report, NVDA has no report
    triage_engine = SignalTriageEngine(e2e_config)
    with patch.object(triage_engine.csv_parser, "parse", return_value=mock_portfolio):
        plan = triage_engine.run_triage(as_of_date=today)

    assert len(plan.queue) == 2
    # AAPL is held (priority 1), NVDA is candidate (priority 2)
    assert plan.queue[0].ticker == "AAPL"
    assert plan.queue[1].ticker == "NVDA"

    # 3. Simulate TradingAgents generating reports into vault for NVDA and AAPL
    # NVDA gets an OVERWEIGHT rating, AAPL gets EQUAL_WEIGHT
    nvda_report_folder = reports_dir / "reports" / "NVDA_20260907_120000" / "5_portfolio"
    nvda_report_folder.mkdir(parents=True, exist_ok=True)
    (nvda_report_folder / "decision.md").write_text(
        "**Rating**: Overweight\n**Price Target**: 140.0\n**Executive Summary**: Strong upside.",
        encoding="utf-8",
    )

    aapl_report_folder = reports_dir / "reports" / "AAPL_20260907_120000" / "5_portfolio"
    aapl_report_folder.mkdir(parents=True, exist_ok=True)
    (aapl_report_folder / "decision.md").write_text(
        "**Rating**: Hold\n**Price Target**: 160.0\n**Executive Summary**: Maintain exposure.",
        encoding="utf-8",
    )

    # 4. Now run PortfolioManagerEngine solver
    engine = PortfolioManagerEngine.__new__(PortfolioManagerEngine)
    engine.config = e2e_config
    engine.reports_dir = str(reports_dir)
    engine.downloads_dir = e2e_config["paths"]["downloads_dir"]
    engine.obsidian_vault_dir = str(reports_dir)
    engine.max_age_days = 14
    engine.stale_warning_days = 4
    engine.candidate_min_signal = SignalType.OVERWEIGHT
    engine.equivalent_symbols = [["GOOG", "GOOGL"]]
    engine.enforce_cash_reserve = True

    from pm.ingest.markdown_signals import MarkdownSignalParser
    from pm.ingest.broker_csv import BrokerCSVParser
    from pm.market_data.pricing import MarketDataService
    from pm.risk.clustering import CorrelationClusterEngine
    from pm.risk.constraints import PortfolioConstraintOptimizer
    from pm.engine.reconciler import PortfolioReconciler

    engine.signal_parser = MarkdownSignalParser(engine.reports_dir, max_age_days=14, stale_warning_days=4)
    engine.csv_parser = BrokerCSVParser(engine.downloads_dir)
    engine.market_data = MarketDataService()
    engine.cluster_engine = CorrelationClusterEngine(cophenetic_threshold=0.5)
    engine.optimizer = PortfolioConstraintOptimizer(
        max_position_weight=0.15,
        min_cash_reserve=0.15,
        max_cluster_exposure=0.25,
        multipliers={
            SignalType.OVERWEIGHT: 1.5,
            SignalType.EQUAL_WEIGHT: 1.0,
            SignalType.UNDERWEIGHT: 0.5,
            SignalType.AVOID: 0.0,
        },
        market_cap_cfg=e2e_config["market_cap_weighting"],
    )
    engine.reconciler = PortfolioReconciler(
        relative_threshold=0.20,
        hold_drift_tolerance=1.00,
        min_dollar_trade=1500.0,
        prefer_whole_shares=True,
        liquidate_avoid=True,
        stepping_enabled=True,
        starter_step_factor=0.50,
        rebalance_step_factor=0.50,
        max_trade_dollar_cap=6000.0,
        avoid_step_factor=1.00,
    )

    import pandas as pd
    mock_prices = {"AAPL": 150.0, "NVDA": 120.0}
    mock_caps = {"AAPL": 3_000_000_000_000, "NVDA": 2_800_000_000_000}
    mock_returns = pd.DataFrame({
        "AAPL": [0.01, -0.02, 0.015, -0.01],
        "NVDA": [0.02, -0.01, 0.03, -0.005],
    })

    with patch.object(engine.csv_parser, "parse", return_value=mock_portfolio), \
         patch.object(engine.market_data, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(engine.market_data, "fetch_market_caps", return_value=mock_caps), \
         patch.object(engine.market_data, "fetch_historical_returns", return_value=mock_returns):

        summary = engine.run_solver(as_of_date=today)

    # 5. Verify the complete handoff!
    # NVDA (new candidate with OVERWEIGHT signal) should enter the universe and receive a BUY order
    alloc_map = {a.ticker: a for a in summary.allocations}
    assert "NVDA" in alloc_map
    assert "AAPL" in alloc_map

    nvda_alloc = alloc_map["NVDA"]
    assert nvda_alloc.signal == SignalType.OVERWEIGHT
    assert nvda_alloc.action == "BUY"
    assert nvda_alloc.order_shares > 0
    assert "STARTER_TRANCHE" in nvda_alloc.reason
    # Verify report_path and report_date are attached to allocations
    assert nvda_alloc.report_path == str(nvda_report_folder / "decision.md")
    assert nvda_alloc.report_date == today
    assert alloc_map["AAPL"].report_path == str(aapl_report_folder / "decision.md")
    assert alloc_map["AAPL"].report_date == today

    # Verify cash reserve guard preserved target cash
    assert summary.projected_ending_cash >= summary.target_cash_reserve


def test_whitelist_guardrail_blocks_unapproved_etfs(e2e_config, tmp_path):
    today = date(2026, 9, 7)
    reports_dir = Path(e2e_config["paths"]["reports_dir"])

    mock_holdings = {
        "AAPL": Holding(symbol="AAPL", description="Apple", quantity=20, last_price=150.0, current_value=3000.0),
    }
    mock_portfolio = PortfolioState(
        total_account_value=30000.0,
        cash_balance=27000.0,
        cash_percent=90.0,
        holdings=mock_holdings,
    )

    # In reports dir, create OVERWEIGHT reports for NVDA (approved in categories), VOO (unapproved ETF), BND (unapproved bond)
    nvda_dir = reports_dir / "reports" / "NVDA_20260907_130000" / "5_portfolio"
    nvda_dir.mkdir(parents=True, exist_ok=True)
    (nvda_dir / "decision.md").write_text(
        "**Rating**: Overweight\n**Price Target**: 140.0\n**Executive Summary**: Strong upside.",
        encoding="utf-8",
    )

    voo_dir = reports_dir / "reports" / "VOO_20260907_130000" / "5_portfolio"
    voo_dir.mkdir(parents=True, exist_ok=True)
    (voo_dir / "decision.md").write_text(
        "**Rating**: Overweight\n**Price Target**: 500.0\n**Executive Summary**: Broad market index.",
        encoding="utf-8",
    )

    bnd_dir = reports_dir / "reports" / "BND_20260907_130000" / "5_portfolio"
    bnd_dir.mkdir(parents=True, exist_ok=True)
    (bnd_dir / "decision.md").write_text(
        "**Rating**: Overweight\n**Price Target**: 80.0\n**Executive Summary**: Bond market index.",
        encoding="utf-8",
    )

    engine = PortfolioManagerEngine.__new__(PortfolioManagerEngine)
    engine.config = e2e_config
    engine.config_path = str(tmp_path / "config.yaml")
    engine.reports_dir = str(reports_dir)
    engine.downloads_dir = e2e_config["paths"]["downloads_dir"]
    engine.obsidian_vault_dir = str(reports_dir)
    engine.max_age_days = 14
    engine.stale_warning_days = 4
    engine.candidate_min_signal = SignalType.OVERWEIGHT
    engine.equivalent_symbols = [["GOOG", "GOOGL"]]
    engine.enforce_cash_reserve = True
    engine.restrict_candidates_to_watchlist = True
    engine.excluded_tickers = set()
    engine.watchlist_cfg = e2e_config["watchlist"]

    from pm.ingest.markdown_signals import MarkdownSignalParser
    from pm.ingest.broker_csv import BrokerCSVParser
    from pm.market_data.pricing import MarketDataService
    from pm.risk.clustering import CorrelationClusterEngine
    from pm.risk.constraints import PortfolioConstraintOptimizer
    from pm.engine.reconciler import PortfolioReconciler

    engine.signal_parser = MarkdownSignalParser(engine.reports_dir, max_age_days=14, stale_warning_days=4)
    engine.csv_parser = BrokerCSVParser(engine.downloads_dir)
    engine.market_data = MarketDataService()
    engine.cluster_engine = CorrelationClusterEngine(cophenetic_threshold=0.5)
    engine.optimizer = PortfolioConstraintOptimizer(
        max_position_weight=0.15,
        min_cash_reserve=0.15,
        max_cluster_exposure=0.25,
        multipliers={
            SignalType.OVERWEIGHT: 1.5,
            SignalType.EQUAL_WEIGHT: 1.0,
            SignalType.UNDERWEIGHT: 0.5,
            SignalType.AVOID: 0.0,
        },
        market_cap_cfg=e2e_config["market_cap_weighting"],
    )
    engine.reconciler = PortfolioReconciler(
        relative_threshold=0.20,
        hold_drift_tolerance=1.00,
        min_dollar_trade=1500.0,
        prefer_whole_shares=True,
        liquidate_avoid=True,
        stepping_enabled=True,
        starter_step_factor=0.50,
        rebalance_step_factor=0.50,
        max_trade_dollar_cap=6000.0,
        avoid_step_factor=1.00,
    )

    import pandas as pd
    mock_prices = {"AAPL": 150.0, "NVDA": 120.0, "VOO": 500.0, "BND": 80.0}
    mock_caps = {"AAPL": 3_000_000_000_000, "NVDA": 2_800_000_000_000, "VOO": 1_000_000_000_000, "BND": 100_000_000_000}
    mock_returns = pd.DataFrame({
        "AAPL": [0.01, -0.02, 0.015, -0.01],
        "NVDA": [0.02, -0.01, 0.03, -0.005],
    })

    with patch.object(engine.csv_parser, "parse", return_value=mock_portfolio), \
         patch.object(engine.market_data, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(engine.market_data, "fetch_market_caps", return_value=mock_caps), \
         patch.object(engine.market_data, "fetch_historical_returns", return_value=mock_returns):

        summary = engine.run_solver(as_of_date=today)

    allocated_tickers = {a.ticker for a in summary.allocations}

    # NVDA was admitted and allocated
    assert "NVDA" in allocated_tickers
    assert "AAPL" in allocated_tickers

    # VOO and BND were excluded from universe and received zero allocations or orders
    assert "VOO" not in allocated_tickers
    assert "BND" not in allocated_tickers
    order_tickers = {a.ticker for a in summary.allocations if a.order_shares > 0}
    assert "VOO" not in order_tickers
    assert "BND" not in order_tickers


def test_candidate_exclusion_filters_blacklisted_symbol(e2e_config, tmp_path):
    today = date(2026, 9, 7)
    reports_dir = Path(e2e_config["paths"]["reports_dir"])

    mock_holdings = {
        "AAPL": Holding(symbol="AAPL", description="Apple", quantity=20, last_price=150.0, current_value=3000.0),
    }
    mock_portfolio = PortfolioState(
        total_account_value=30000.0,
        cash_balance=27000.0,
        cash_percent=90.0,
        holdings=mock_holdings,
    )

    nvda_dir = reports_dir / "reports" / "NVDA_20260907_140000" / "5_portfolio"
    nvda_dir.mkdir(parents=True, exist_ok=True)
    (nvda_dir / "decision.md").write_text(
        "**Rating**: Overweight\n**Price Target**: 140.0\n**Executive Summary**: Strong upside.",
        encoding="utf-8",
    )

    engine = PortfolioManagerEngine.__new__(PortfolioManagerEngine)
    engine.config = e2e_config
    engine.config_path = str(tmp_path / "config.yaml")
    engine.reports_dir = str(reports_dir)
    engine.downloads_dir = e2e_config["paths"]["downloads_dir"]
    engine.obsidian_vault_dir = str(reports_dir)
    engine.max_age_days = 14
    engine.stale_warning_days = 4
    engine.candidate_min_signal = SignalType.OVERWEIGHT
    engine.equivalent_symbols = [["GOOG", "GOOGL"]]
    engine.enforce_cash_reserve = True
    engine.restrict_candidates_to_watchlist = True
    # NVDA explicitly blacklisted
    engine.excluded_tickers = {"NVDA"}
    engine.watchlist_cfg = e2e_config["watchlist"]

    from pm.ingest.markdown_signals import MarkdownSignalParser
    from pm.ingest.broker_csv import BrokerCSVParser
    from pm.market_data.pricing import MarketDataService
    from pm.risk.clustering import CorrelationClusterEngine
    from pm.risk.constraints import PortfolioConstraintOptimizer
    from pm.engine.reconciler import PortfolioReconciler

    engine.signal_parser = MarkdownSignalParser(engine.reports_dir, max_age_days=14, stale_warning_days=4)
    engine.csv_parser = BrokerCSVParser(engine.downloads_dir)
    engine.market_data = MarketDataService()
    engine.cluster_engine = CorrelationClusterEngine(cophenetic_threshold=0.5)
    engine.optimizer = PortfolioConstraintOptimizer(
        max_position_weight=0.15,
        min_cash_reserve=0.15,
        max_cluster_exposure=0.25,
        multipliers={
            SignalType.OVERWEIGHT: 1.5,
            SignalType.EQUAL_WEIGHT: 1.0,
            SignalType.UNDERWEIGHT: 0.5,
            SignalType.AVOID: 0.0,
        },
        market_cap_cfg=e2e_config["market_cap_weighting"],
    )
    engine.reconciler = PortfolioReconciler(
        relative_threshold=0.20,
        hold_drift_tolerance=1.00,
        min_dollar_trade=1500.0,
        prefer_whole_shares=True,
        liquidate_avoid=True,
        stepping_enabled=True,
        starter_step_factor=0.50,
        rebalance_step_factor=0.50,
        max_trade_dollar_cap=6000.0,
        avoid_step_factor=1.00,
    )

    import pandas as pd
    mock_prices = {"AAPL": 150.0}
    mock_caps = {"AAPL": 3_000_000_000_000}
    mock_returns = pd.DataFrame({"AAPL": [0.01, -0.02, 0.015, -0.01]})

    with patch.object(engine.csv_parser, "parse", return_value=mock_portfolio), \
         patch.object(engine.market_data, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(engine.market_data, "fetch_market_caps", return_value=mock_caps), \
         patch.object(engine.market_data, "fetch_historical_returns", return_value=mock_returns):

        summary = engine.run_solver(as_of_date=today)

    allocated_tickers = {a.ticker for a in summary.allocations}
    assert "NVDA" not in allocated_tickers
    assert "AAPL" in allocated_tickers
