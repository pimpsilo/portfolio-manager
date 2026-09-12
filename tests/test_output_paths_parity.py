import csv
import re
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import pytest
import yaml

from pm.engine.controller import PortfolioManagerEngine
from pm.market_data.pricing import MarketDataService
from pm.models import Holding, PortfolioState, SignalType
from pm.output.reporter import MarkdownTradeReporter
from pm.watcher import DownloadWatcher
import main


def _make_sample_csv(path: Path, download_time: str, cash: float = 50000.0):
    content = f""""Brokerage"
"Account : XXXXXXXX"
"Account Total","350000.00"
"Total Cash","{cash:.2f}"
"Margin Balance","0.00"
"Symbol","Description","Quantity","Last price","Current value"
"AAPL","APPLE INC",19,330.00,6270.00
"NVDA","NVIDIA CORP",50,220.00,11000.00
"FDRXX","HELD IN MONEY MARKET",{cash:.2f},1.00,{cash:.2f}

"Date downloaded {download_time}"
"""
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def parity_env(tmp_path):
    reports_dir = tmp_path / "portfolio" / "01_agent_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    vault_dir = tmp_path / "portfolio" / "00_trade_orders"
    vault_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write stocks.csv
    stocks_file = tmp_path / "stocks.csv"
    stocks_file.write_text("AAPL\nNVDA\n", encoding="utf-8")

    # 2. Write agent reports
    today = date(2026, 9, 12)
    aapl_dir = reports_dir / "AAPL" / f"AAPL_{today.strftime('%Y%m%d')}_095000" / "5_portfolio"
    aapl_dir.mkdir(parents=True, exist_ok=True)
    (aapl_dir / "decision.md").write_text(
        "**Rating**: Overweight\n**Target**: $380.00\n**Summary**: High conviction growth.",
        encoding="utf-8",
    )
    (aapl_dir.parent / "complete_report.md").write_text(
        "# AAPL Complete Report\nRating: Overweight\nTarget: $380.00\nMaintain Overweight.",
        encoding="utf-8",
    )

    nvda_dir = reports_dir / "NVDA" / f"NVDA_{today.strftime('%Y%m%d')}_095000" / "5_portfolio"
    nvda_dir.mkdir(parents=True, exist_ok=True)
    (nvda_dir / "decision.md").write_text(
        "**Rating**: Hold\n**Target**: $240.00\n**Summary**: High valuation neutral.",
        encoding="utf-8",
    )
    (nvda_dir.parent / "complete_report.md").write_text(
        "# NVDA Complete Report\nRating: Hold\nTarget: $240.00\nHold current allocation.",
        encoding="utf-8",
    )

    # 3. Create sample CSV files
    csv1 = downloads_dir / "Portfolio_Positions_Sep-12-2026.csv"
    csv2 = downloads_dir / "Portfolio_Positions_Sep-12-2026 (1).csv"
    csv3 = downloads_dir / "Portfolio_Positions_Sep-12-2026 (2).csv"
    _make_sample_csv(csv1, "Sep-12-2026 9:43 a.m ET", cash=60000.0)
    _make_sample_csv(csv2, "Sep-12-2026 3:10 p.m ET", cash=62000.0)
    _make_sample_csv(csv3, "Sep-12-2026 3:16 p.m ET", cash=63893.10)

    # 4. Write config.yaml
    cfg = {
        "paths": {
            "reports_dir": str(reports_dir),
            "downloads_dir": str(downloads_dir),
            "obsidian_vault_dir": str(vault_dir),
        },
        "signals": {
            "max_age_days": 14,
            "stale_warning_days": 4,
            "candidate_min_signal": "OVERWEIGHT",
            "restrict_candidates_to_watchlist": True,
            "excluded_tickers": [],
        },
        "equivalent_symbols": [["GOOG", "GOOGL"]],
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
            "stocks_file": str(stocks_file),
            "max_batch_size": 5,
            "rate_limit_delay_seconds": 0.0,
            "categories": {},
        },
        "output": {
            "report_link_style": "markdown",
            "prefer_complete_report": True,
            "date_layout": "stacked",
            "append_daily_snapshots": True,
        },
        "watcher": {
            "poll_interval_seconds": 1,
            "debounce_seconds": 0,
        },
    }
    cfg_file = tmp_path / "config.yaml"
    with open(cfg_file, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)

    return {
        "tmp_path": tmp_path,
        "config_file": cfg_file,
        "vault_dir": vault_dir,
        "reports_dir": reports_dir,
        "downloads_dir": downloads_dir,
        "csv1": csv1,
        "csv2": csv2,
        "csv3": csv3,
    }


def _mock_market_data():
    mock_prices = {"AAPL": 332.27, "NVDA": 218.29}
    mock_caps = {"AAPL": 3_000_000_000_000, "NVDA": 2_500_000_000_000}
    mock_returns = pd.DataFrame({
        "AAPL": [0.01, -0.01, 0.02, 0.005, -0.015],
        "NVDA": [0.02, -0.015, 0.03, 0.01, -0.02],
    })
    return mock_prices, mock_caps, mock_returns


def _normalize_content(text: str) -> str:
    """Strips execution timestamps for exact content comparison across runs."""
    return re.sub(r"\*\*Solver Run\*\*: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "**Solver Run**: MOCKED_TIMESTAMP", text)


def test_output_paths_single_run_structural_parity(parity_env):
    """
    Verifies that all four execution paths (Direct Reporter, Watcher, Manual CLI, Pipeline)
    produce identical output file structure, headers, and content on the same input CSV.
    """
    mock_prices, mock_caps, mock_returns = _mock_market_data()
    csv1 = parity_env["csv1"]
    cfg_file = parity_env["config_file"]

    # 1. Path A: Direct Reporter Update
    vault_a = parity_env["tmp_path"] / "vault_a"
    vault_a.mkdir()
    with patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        engine_a = PortfolioManagerEngine(config_path=str(cfg_file))
        summary_a = engine_a.run_solver(csv_path=str(csv1))
        reporter_a = MarkdownTradeReporter(output_dir=str(vault_a), append_daily_snapshots=True)
        path_a = reporter_a.write_report(summary_a)
        content_a = _normalize_content(path_a.read_text(encoding="utf-8"))

    # 2. Path B: Watcher Automation (process_file)
    vault_b = parity_env["tmp_path"] / "vault_b"
    vault_b.mkdir()
    cfg_b = dict(engine_a.config)
    cfg_b["paths"]["obsidian_vault_dir"] = str(vault_b)
    cfg_file_b = parity_env["tmp_path"] / "config_b.yaml"
    cfg_file_b.write_text(yaml.dump(cfg_b), encoding="utf-8")

    with patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        watcher_b = DownloadWatcher(config_path=str(cfg_file_b))
        summary_b, path_b = watcher_b.process_file(csv1)
        content_b = _normalize_content(path_b.read_text(encoding="utf-8"))

    # 3. Path C: Manual CLI Prompt (main.py --execute)
    vault_c = parity_env["tmp_path"] / "vault_c"
    vault_c.mkdir()
    cfg_c = dict(engine_a.config)
    cfg_c["paths"]["obsidian_vault_dir"] = str(vault_c)
    cfg_file_c = parity_env["tmp_path"] / "config_c.yaml"
    cfg_file_c.write_text(yaml.dump(cfg_c), encoding="utf-8")

    cli_argv = ["main.py", "--config", str(cfg_file_c), "--csv", str(csv1), "--execute"]
    with patch.object(sys, "argv", cli_argv), \
         patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        main.main()
    path_c = vault_c / "Trade_Orders_2026-09-12.md"
    assert path_c.exists()
    content_c = _normalize_content(path_c.read_text(encoding="utf-8"))

    # 4. Path D: Pipeline (main.py --pipeline --execute)
    vault_d = parity_env["tmp_path"] / "vault_d"
    vault_d.mkdir()
    cfg_d = dict(engine_a.config)
    cfg_d["paths"]["obsidian_vault_dir"] = str(vault_d)
    cfg_file_d = parity_env["tmp_path"] / "config_d.yaml"
    cfg_file_d.write_text(yaml.dump(cfg_d), encoding="utf-8")

    pipeline_argv = ["main.py", "--config", str(cfg_file_d), "--csv", str(csv1), "--pipeline", "--execute"]
    with patch.object(sys, "argv", pipeline_argv), \
         patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        main.main()
    path_d = vault_d / "Trade_Orders_2026-09-12.md"
    assert path_d.exists()
    content_d = _normalize_content(path_d.read_text(encoding="utf-8"))

    # Assert 100% exact parity across ALL four paths
    assert content_a == content_b, "Watcher output differs from Direct Reporter output"
    assert content_a == content_c, "Manual CLI output differs from Direct Reporter output"
    assert content_a == content_d, "Pipeline output differs from Direct Reporter output"

    # Verify structural sections in all outputs
    for c in (content_a, content_b, content_c, content_d):
        assert "# Trade Execution Orders — 2026-09-12" in c
        assert "## ⏱️ Snapshot: Sep-12-2026 9:43 a.m ET (Source: `Portfolio_Positions_Sep-12-2026.csv`)" in c
        assert "## 🎯 1. Immediate / Daily Actions" in c
        assert "## ⏳ 2. Aging / Stale Reports Summary" in c
        assert "## 📊 3. Full Portfolio Rebalance & Drift Ledger" in c
        assert "## 🌐 4. Non-Portfolio Securities with Active Agent Reports & Status" in c
        assert "## 📋 5. All Securities with Agent Reports" in c
        assert "## 🔗 Correlated Asset Clusters & Exposure" in c
        assert "HLIT" not in c
        assert "SPY" not in c


def test_output_paths_multi_snapshot_append_parity(parity_env):
    """
    Verifies that sequential runs across different paths (Manual CLI -> Watcher -> Direct Reporter)
    append intra-day snapshots cleanly into a single daily markdown report with '---' dividers.
    """
    mock_prices, mock_caps, mock_returns = _mock_market_data()
    csv1 = parity_env["csv1"]
    csv2 = parity_env["csv2"]
    csv3 = parity_env["csv3"]
    cfg_file = parity_env["config_file"]
    vault_dir = parity_env["vault_dir"]

    # Step 1: Run Manual CLI on CSV 1 (09:43)
    cli_argv = ["main.py", "--config", str(cfg_file), "--csv", str(csv1), "--execute"]
    with patch.object(sys, "argv", cli_argv), \
         patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        main.main()

    report_path = vault_dir / "Trade_Orders_2026-09-12.md"
    assert report_path.exists()
    c1 = report_path.read_text(encoding="utf-8")
    assert c1.count("# Trade Execution Orders — 2026-09-12") == 1
    assert c1.count("## ⏱️ Snapshot:") == 1
    assert "Portfolio_Positions_Sep-12-2026.csv" in c1
    assert "Sep-12-2026 9:43 a.m ET" in c1
    assert "\n\n---\n\n" not in c1

    # Step 2: Run Watcher on CSV 2 (15:10)
    with patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        watcher = DownloadWatcher(config_path=str(cfg_file))
        summary2, path2 = watcher.process_file(csv2)

    c2 = report_path.read_text(encoding="utf-8")
    assert c2.count("# Trade Execution Orders — 2026-09-12") == 1
    assert c2.count("## ⏱️ Snapshot:") == 2
    assert c2.count("\n\n---\n\n") == 1
    assert "Portfolio_Positions_Sep-12-2026.csv" in c2
    assert "Portfolio_Positions_Sep-12-2026 (1).csv" in c2
    assert "Sep-12-2026 3:10 p.m ET" in c2

    # Step 3: Run Direct Reporter on CSV 3 (15:16)
    with patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        engine = PortfolioManagerEngine(config_path=str(cfg_file))
        summary3 = engine.run_solver(csv_path=str(csv3))
        reporter = MarkdownTradeReporter(output_dir=str(vault_dir), append_daily_snapshots=True)
        reporter.write_report(summary3)

    c3 = report_path.read_text(encoding="utf-8")
    assert c3.count("# Trade Execution Orders — 2026-09-12") == 1
    assert c3.count("## ⏱️ Snapshot:") == 3
    assert c3.count("\n\n---\n\n") == 2
    assert "Portfolio_Positions_Sep-12-2026.csv" in c3
    assert "Portfolio_Positions_Sep-12-2026 (1).csv" in c3
    assert "Portfolio_Positions_Sep-12-2026 (2).csv" in c3
    assert "Sep-12-2026 3:16 p.m ET" in c3


def test_output_paths_idempotency_cross_path(parity_env):
    """
    Verifies that re-running the same CSV across different paths (e.g. Watcher followed by CLI)
    replaces that CSV's snapshot block in-place without duplicating blocks or horizontal rules.
    """
    mock_prices, mock_caps, mock_returns = _mock_market_data()
    csv1 = parity_env["csv1"]
    csv2 = parity_env["csv2"]
    cfg_file = parity_env["config_file"]
    vault_dir = parity_env["vault_dir"]

    # Process CSV 1 and CSV 2 via Watcher
    with patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        watcher = DownloadWatcher(config_path=str(cfg_file))
        watcher.process_file(csv1)
        watcher.process_file(csv2)

    report_path = vault_dir / "Trade_Orders_2026-09-12.md"
    c_before = report_path.read_text(encoding="utf-8")
    assert c_before.count("## ⏱️ Snapshot:") == 2
    assert c_before.count("\n\n---\n\n") == 1

    # Re-run CSV 2 via Manual CLI prompt
    cli_argv = ["main.py", "--config", str(cfg_file), "--csv", str(csv2), "--execute"]
    with patch.object(sys, "argv", cli_argv), \
         patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        main.main()

    c_after = report_path.read_text(encoding="utf-8")
    assert c_after.count("## ⏱️ Snapshot:") == 2, "Re-run must not create duplicate snapshot"
    assert c_after.count("\n\n---\n\n") == 1, "Divider count must remain exactly 1"
    assert c_after.count("Portfolio_Positions_Sep-12-2026 (1).csv") == 2  # Once in header, once in metadata line


def test_output_paths_overwrite_flag_parity(parity_env):
    """
    Verifies that --overwrite (CLI) and overwrite=True (Watcher / Reporter) reset
    a multi-snapshot file back to a single snapshot.
    """
    mock_prices, mock_caps, mock_returns = _mock_market_data()
    csv1 = parity_env["csv1"]
    csv2 = parity_env["csv2"]
    cfg_file = parity_env["config_file"]
    vault_dir = parity_env["vault_dir"]

    # Create 2 snapshots
    with patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        watcher = DownloadWatcher(config_path=str(cfg_file))
        watcher.process_file(csv1)
        watcher.process_file(csv2)

    report_path = vault_dir / "Trade_Orders_2026-09-12.md"
    assert report_path.read_text(encoding="utf-8").count("## ⏱️ Snapshot:") == 2

    # Overwrite using CLI --overwrite
    cli_argv = ["main.py", "--config", str(cfg_file), "--csv", str(csv2), "--execute", "--overwrite"]
    with patch.object(sys, "argv", cli_argv), \
         patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        main.main()

    c_overwritten = report_path.read_text(encoding="utf-8")
    assert c_overwritten.count("## ⏱️ Snapshot:") == 1
    assert "Portfolio_Positions_Sep-12-2026.csv" not in c_overwritten
    assert "Portfolio_Positions_Sep-12-2026 (1).csv" in c_overwritten
    assert "\n\n---\n\n" not in c_overwritten


def test_output_paths_legacy_format_auto_upgrade(parity_env):
    """
    Verifies that when a file exists without '## ⏱️ Snapshot:' (legacy format),
    all paths automatically replace the legacy structure with a clean snapshot layout.
    """
    mock_prices, mock_caps, mock_returns = _mock_market_data()
    csv1 = parity_env["csv1"]
    cfg_file = parity_env["config_file"]
    vault_dir = parity_env["vault_dir"]
    report_path = vault_dir / "Trade_Orders_2026-09-12.md"

    # Pre-populate with legacy pre-snapshot report (old Thursday layout)
    legacy_content = (
        "# Trade Execution Orders — 2026-09-12\n\n"
        "> **Portfolio Live Value**: **$367,822.53**\n\n"
        "## 🎯 Execution Directives (Actionable Trades)\n"
        "| Ticker | Action |\n"
        "| HLIT | BUY |\n"
    )
    report_path.write_text(legacy_content, encoding="utf-8")

    # Run Watcher process_file on CSV 1
    with patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        watcher = DownloadWatcher(config_path=str(cfg_file))
        watcher.process_file(csv1)

    c_upgraded = report_path.read_text(encoding="utf-8")
    assert "## ⏱️ Snapshot:" in c_upgraded
    assert "## 🎯 1. Immediate / Daily Actions" in c_upgraded
    assert "## 📋 5. All Securities with Agent Reports" in c_upgraded
    assert "HLIT" not in c_upgraded, "Legacy content must be cleanly replaced, not appended below"


def test_output_paths_watcher_overwrite_parity(parity_env):
    """
    Verifies that watcher.process_file(..., overwrite=True) also resets multi-snapshot file.
    """
    mock_prices, mock_caps, mock_returns = _mock_market_data()
    csv1 = parity_env["csv1"]
    csv2 = parity_env["csv2"]
    cfg_file = parity_env["config_file"]
    vault_dir = parity_env["vault_dir"]

    with patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        watcher = DownloadWatcher(config_path=str(cfg_file))
        watcher.process_file(csv1)
        watcher.process_file(csv2)

    report_path = vault_dir / "Trade_Orders_2026-09-12.md"
    assert report_path.read_text(encoding="utf-8").count("## ⏱️ Snapshot:") == 2

    with patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        watcher.process_file(csv2, overwrite=True)

    c_overwritten = report_path.read_text(encoding="utf-8")
    assert c_overwritten.count("## ⏱️ Snapshot:") == 1
    assert "Portfolio_Positions_Sep-12-2026.csv" not in c_overwritten
    assert "Portfolio_Positions_Sep-12-2026 (1).csv" in c_overwritten


def test_output_paths_mixed_sequence_symmetry(parity_env):
    """
    Verifies that mixing paths in any arbitrary sequence (Watcher -> CLI -> Pipeline)
    preserves full structural integrity and appends snapshots in exact chronological order.
    """
    mock_prices, mock_caps, mock_returns = _mock_market_data()
    csv1 = parity_env["csv1"]
    csv2 = parity_env["csv2"]
    csv3 = parity_env["csv3"]
    cfg_file = parity_env["config_file"]
    vault_dir = parity_env["vault_dir"]

    # 1. Watcher processes CSV 1
    with patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        watcher = DownloadWatcher(config_path=str(cfg_file))
        watcher.process_file(csv1)

    # 2. CLI processes CSV 2
    cli_argv = ["main.py", "--config", str(cfg_file), "--csv", str(csv2), "--execute"]
    with patch.object(sys, "argv", cli_argv), \
         patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        main.main()

    # 3. Pipeline processes CSV 3
    pipeline_argv = ["main.py", "--config", str(cfg_file), "--csv", str(csv3), "--pipeline", "--execute"]
    with patch.object(sys, "argv", pipeline_argv), \
         patch.object(MarketDataService, "fetch_realtime_prices", return_value=mock_prices), \
         patch.object(MarketDataService, "fetch_market_caps", return_value=mock_caps), \
         patch.object(MarketDataService, "fetch_historical_returns", return_value=mock_returns):
        main.main()

    report_path = vault_dir / "Trade_Orders_2026-09-12.md"
    content = report_path.read_text(encoding="utf-8")

    assert content.count("# Trade Execution Orders — 2026-09-12") == 1
    assert content.count("## ⏱️ Snapshot:") == 3
    assert content.count("\n\n---\n\n") == 2

    # Verify chronological sequence
    pos1 = content.index("Portfolio_Positions_Sep-12-2026.csv")
    pos2 = content.index("Portfolio_Positions_Sep-12-2026 (1).csv")
    pos3 = content.index("Portfolio_Positions_Sep-12-2026 (2).csv")
    assert pos1 < pos2 < pos3, "Snapshots must appear in exact order of processing"
