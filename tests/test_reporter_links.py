import os
from datetime import date
from pathlib import Path
import pytest

from pm.models import AllocationResult, ParsedSignal, ReconciliationSummary, SignalType
from pm.output.reporter import MarkdownTradeReporter


@pytest.fixture
def sample_vault(tmp_path):
    vault_dir = tmp_path / "00_trade_orders"
    reports_dir = tmp_path / "01_agent_reports"
    vault_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)

    # Create run directory structure for AAPL
    aapl_run = reports_dir / "AAPL" / "AAPL_20260905_105303"
    aapl_portfolio = aapl_run / "5_portfolio"
    aapl_portfolio.mkdir(parents=True)
    (aapl_portfolio / "decision.md").write_text("Decision content", encoding="utf-8")
    (aapl_run / "complete_report.md").write_text("Complete report content", encoding="utf-8")

    # Create run directory structure for MU (only decision.md, no complete_report.md)
    mu_run = reports_dir / "MU" / "MU_20260905_113258"
    mu_portfolio = mu_run / "5_portfolio"
    mu_portfolio.mkdir(parents=True)
    (mu_portfolio / "decision.md").write_text("MU decision content", encoding="utf-8")

    # Create dashboard file
    dashboard_file = reports_dir / "00_Portfolio_Actions_Dashboard.md"
    dashboard_file.write_text("# Dashboard\n", encoding="utf-8")

    return {
        "vault_dir": vault_dir,
        "reports_dir": reports_dir,
        "aapl_source": str(aapl_portfolio / "decision.md"),
        "aapl_complete": str(aapl_run / "complete_report.md"),
        "mu_source": str(mu_portfolio / "decision.md"),
        "dashboard_file": str(dashboard_file),
    }


def test_reporter_upgrades_to_complete_report(sample_vault):
    reporter = MarkdownTradeReporter(output_dir=str(sample_vault["vault_dir"]))
    result = reporter._report_md_link(
        ticker="AAPL",
        source_path=sample_vault["aapl_source"],
        report_date=date(2026, 9, 5),
        bold=True,
        include_date=True,
    )
    expected_rel = "../01_agent_reports/AAPL/AAPL_20260905_105303/complete_report.md"
    expected_badge = '<span style="font-size: 8pt; opacity: 0.7;">2026-09-05</span>'
    assert f"**[AAPL]({expected_rel})**<br>{expected_badge}" == result


def test_reporter_extracts_date_from_run_dir_when_date_omitted(sample_vault):
    reporter = MarkdownTradeReporter(output_dir=str(sample_vault["vault_dir"]))
    result = reporter._report_md_link(
        ticker="AAPL",
        source_path=sample_vault["aapl_source"],
        report_date=None,
        bold=True,
        include_date=True,
    )
    expected_rel = "../01_agent_reports/AAPL/AAPL_20260905_105303/complete_report.md"
    expected_badge = '<span style="font-size: 8pt; opacity: 0.7;">2026-09-05</span>'
    assert f"**[AAPL]({expected_rel})**<br>{expected_badge}" == result


def test_reporter_inline_date_layout(sample_vault):
    reporter = MarkdownTradeReporter(
        output_dir=str(sample_vault["vault_dir"]),
        date_layout="inline",
    )
    result = reporter._report_md_link(
        ticker="AAPL",
        source_path=sample_vault["aapl_source"],
        report_date=date(2026, 9, 5),
        bold=True,
        include_date=True,
    )
    expected_rel = "../01_agent_reports/AAPL/AAPL_20260905_105303/complete_report.md"
    expected_badge = '<span style="font-size: 8pt; opacity: 0.7;">2026-09-05</span>'
    assert f"**[AAPL]({expected_rel})** {expected_badge}" == result


def test_reporter_falls_back_when_no_complete_report(sample_vault):
    reporter = MarkdownTradeReporter(output_dir=str(sample_vault["vault_dir"]))
    result = reporter._report_md_link(
        ticker="MU",
        source_path=sample_vault["mu_source"],
        report_date=date(2026, 9, 5),
        bold=False,
        include_date=False,
    )
    expected_rel = "../01_agent_reports/MU/MU_20260905_113258/5_portfolio/decision.md"
    assert f"[MU]({expected_rel})" == result


def test_reporter_bare_ticker_when_no_source(sample_vault):
    reporter = MarkdownTradeReporter(output_dir=str(sample_vault["vault_dir"]))
    bold_res = reporter._report_md_link("CASH_EQ", source_path=None, bold=True)
    plain_res = reporter._report_md_link("CASH_EQ", source_path=None, bold=False)
    assert bold_res == "**CASH_EQ**"
    assert plain_res == "CASH_EQ"


def test_reporter_dashboard_source_link(sample_vault):
    reporter = MarkdownTradeReporter(output_dir=str(sample_vault["vault_dir"]))
    result = reporter._report_md_link(
        ticker="XYZ",
        source_path=sample_vault["dashboard_file"],
        report_date=date(2026, 9, 7),
        bold=True,
        include_date=True,
    )
    expected_rel = "../01_agent_reports/00_Portfolio_Actions_Dashboard.md"
    expected_badge = '<span style="font-size: 8pt; opacity: 0.7;">2026-09-07</span>'
    assert f"**[XYZ]({expected_rel})**<br>{expected_badge}" == result


def test_reporter_link_style_none(sample_vault):
    reporter = MarkdownTradeReporter(
        output_dir=str(sample_vault["vault_dir"]),
        link_style="none",
    )
    result = reporter._report_md_link(
        ticker="AAPL",
        source_path=sample_vault["aapl_source"],
        bold=True,
    )
    assert result == "**AAPL**"


def test_reporter_link_style_wikilink(sample_vault):
    reporter = MarkdownTradeReporter(
        output_dir=str(sample_vault["vault_dir"]),
        link_style="wikilink",
    )
    result = reporter._report_md_link(
        ticker="AAPL",
        source_path=sample_vault["aapl_source"],
        bold=False,
        include_date=False,
    )
    expected_rel = "../01_agent_reports/AAPL/AAPL_20260905_105303/complete_report.md"
    assert result == f"[[{expected_rel}\\|AAPL]]"


def test_full_report_markdown_generation_and_no_wikilinks(sample_vault):
    reporter = MarkdownTradeReporter(output_dir=str(sample_vault["vault_dir"]))

    alloc_aapl = AllocationResult(
        ticker="AAPL",
        current_shares=10.0,
        realtime_price=300.0,
        current_value=3000.0,
        current_weight=30.0,
        signal=SignalType.OVERWEIGHT,
        cluster_id=1,
        base_weight=2.0,
        target_weight=4.0,
        target_value=4000.0,
        dollar_delta=1000.0,
        drift_pct=1.0,
        action="BUY",
        order_shares=3.0,
        is_whole_share=True,
        reason="REBALANCE_BUY_STEP",
        report_path=sample_vault["aapl_source"],
        report_date=date(2026, 9, 5),
    )
    alloc_unrated = AllocationResult(
        ticker="UNRATED",
        current_shares=5.0,
        realtime_price=100.0,
        current_value=500.0,
        current_weight=5.0,
        signal=SignalType.EQUAL_WEIGHT,
        cluster_id=2,
        base_weight=1.0,
        target_weight=1.0,
        target_value=500.0,
        dollar_delta=0.0,
        drift_pct=0.0,
        action="HOLD",
        order_shares=0.0,
        is_whole_share=True,
        reason="DEFAULT_EQUAL_WEIGHT",
        report_path=None,
        report_date=None,
    )

    aging_sig = ParsedSignal(
        ticker="HLIT",
        signal=SignalType.EQUAL_WEIGHT,
        date=date(2026, 8, 31),
        source_path=sample_vault["mu_source"],
        age_days=10,
        days_remaining=4,
        is_approaching_stale=True,
        is_expired=False,
        in_portfolio=False,
    )

    summary = ReconciliationSummary(
        total_portfolio_value=100000.0,
        current_cash=20000.0,
        target_cash_reserve=15000.0,
        projected_ending_cash=19100.0,
        total_buys_dollars=900.0,
        total_sells_dollars=0.0,
        allocations=[alloc_aapl, alloc_unrated],
        clusters={1: ["AAPL"], 2: ["UNRATED"]},
        aging_signals=[aging_sig],
        expired_signals=[],
        max_age_days=14,
        execution_date=date(2026, 9, 10),
    )

    md = reporter.generate_report_markdown(summary)

    # 1. Assert NO wikilinks anywhere in the generated output
    assert "[[" not in md
    assert "]]" not in md

    # 2. Execution Directives table asserts
    assert "**[AAPL](../01_agent_reports/AAPL/AAPL_20260905_105303/complete_report.md)**<br>" in md
    assert '<span style="font-size: 8pt; opacity: 0.7;">2026-09-05</span>' in md

    # 3. Aging table asserts: link present on ticker, but NO date badge inside ticker cell
    assert "**[HLIT](../01_agent_reports/MU/MU_20260905_113258/5_portfolio/decision.md)**" in md
    assert "**[HLIT" in md
    # Ensure HLIT's cell doesn't have an 8pt span
    for line in md.splitlines():
        if "HLIT" in line and "font-size" in line:
            pytest.fail("Found font-size date badge in Aging table row for HLIT")

    # 4. Drift Ledger table asserts
    assert "**UNRATED**" in md

    # 5. Cluster table asserts
    assert "[AAPL](../01_agent_reports/AAPL/AAPL_20260905_105303/complete_report.md)" in md
    assert "UNRATED" in md

    # 6. Test write_report
    written_file = reporter.write_report(summary)
    assert written_file.exists()
    assert written_file.name == "Trade_Orders_2026-09-10.md"
    assert written_file.read_text(encoding="utf-8") == md


def test_five_sections_in_trade_order_report(sample_vault):
    reporter = MarkdownTradeReporter(output_dir=str(sample_vault["vault_dir"]))

    # Directives with multiple sells and buys in non-alphabetical order
    alloc_sell_z = AllocationResult(
        ticker="ZZZ",
        current_shares=10.0,
        realtime_price=100.0,
        current_value=1000.0,
        current_weight=10.0,
        signal=SignalType.AVOID,
        cluster_id=1,
        base_weight=0.0,
        target_weight=0.0,
        target_value=0.0,
        dollar_delta=-1000.0,
        drift_pct=-10.0,
        action="SELL",
        order_shares=10.0,
        is_whole_share=True,
        reason="AVOID_LIQUIDATION",
    )
    alloc_sell_a = AllocationResult(
        ticker="AAA",
        current_shares=5.0,
        realtime_price=200.0,
        current_value=1000.0,
        current_weight=10.0,
        signal=SignalType.UNDERWEIGHT,
        cluster_id=1,
        base_weight=0.0,
        target_weight=0.0,
        target_value=0.0,
        dollar_delta=-1000.0,
        drift_pct=-10.0,
        action="SELL",
        order_shares=5.0,
        is_whole_share=True,
        reason="REBALANCE_TRIM",
    )
    alloc_buy_y = AllocationResult(
        ticker="YYY",
        current_shares=0.0,
        realtime_price=50.0,
        current_value=0.0,
        current_weight=0.0,
        signal=SignalType.OVERWEIGHT,
        cluster_id=2,
        base_weight=2.0,
        target_weight=2.0,
        target_value=2000.0,
        dollar_delta=2000.0,
        drift_pct=2.0,
        action="BUY",
        order_shares=40.0,
        is_whole_share=True,
        reason="NEW_STARTER_ENTRY",
    )
    alloc_buy_b = AllocationResult(
        ticker="BBB",
        current_shares=0.0,
        realtime_price=100.0,
        current_value=0.0,
        current_weight=0.0,
        signal=SignalType.OVERWEIGHT,
        cluster_id=2,
        base_weight=2.0,
        target_weight=2.0,
        target_value=2000.0,
        dollar_delta=2000.0,
        drift_pct=2.0,
        action="BUY",
        order_shares=20.0,
        is_whole_share=True,
        reason="NEW_STARTER_ENTRY",
    )

    # Signals
    sig_held = ParsedSignal(
        ticker="AAA",
        signal=SignalType.UNDERWEIGHT,
        date=date(2026, 9, 5),
        source_path=sample_vault["aapl_source"],
        target_price=180.0,
        in_portfolio=True,
        core_guidance="Trim AAA to reduce defensive exposure.",
    )
    sig_non_port = ParsedSignal(
        ticker="CAVA",
        signal=SignalType.EQUAL_WEIGHT,
        date=date(2026, 9, 5),
        source_path=sample_vault["mu_source"],
        target_price=78.0,
        in_portfolio=False,
        core_guidance="Maintain current exposure in CAVA without deploying new capital.",
    )
    sig_buy_b = ParsedSignal(
        ticker="BBB",
        signal=SignalType.OVERWEIGHT,
        date=date(2026, 9, 5),
        source_path="",
        target_price=120.0,
        in_portfolio=False,
        core_guidance="Initiate overweight position in BBB on momentum.",
    )

    summary = ReconciliationSummary(
        total_portfolio_value=100000.0,
        current_cash=20000.0,
        target_cash_reserve=15000.0,
        projected_ending_cash=18000.0,
        total_buys_dollars=4000.0,
        total_sells_dollars=2000.0,
        allocations=[alloc_sell_z, alloc_sell_a, alloc_buy_y, alloc_buy_b],
        clusters={1: ["AAA", "ZZZ"], 2: ["BBB", "YYY"]},
        aging_signals=[],
        expired_signals=[],
        all_signals=[sig_held, sig_non_port, sig_buy_b],
        max_age_days=14,
        execution_date=date(2026, 9, 11),
    )

    md = reporter.generate_report_markdown(summary)

    # Section 1 checks:
    assert "## 🎯 1. Immediate / Daily Actions" in md
    # Sells sorted alphabetically: AAA before ZZZ
    pos_aaa = md.index("AAA")
    pos_zzz = md.index("ZZZ")
    pos_bbb = md.index("BBB")
    pos_yyy = md.index("YYY")
    assert pos_aaa < pos_zzz, "Sells should be sorted alphabetically"
    assert pos_zzz < pos_bbb, "Sells must precede Buys"
    assert pos_bbb < pos_yyy, "Buys should be sorted alphabetically"

    # Section 2 checks:
    assert "## ⏳ 2. Aging / Stale Reports Summary" in md

    # Section 3 checks:
    assert "## 📊 3. Full Portfolio Rebalance & Drift Ledger" in md

    # Section 4 checks:
    assert "## 🌐 4. Non-Portfolio Securities with Active Agent Reports & Status" in md
    assert "CAVA" in md
    assert "BBB" in md

    # Section 5 checks:
    assert "## 📋 5. All Securities with Agent Reports" in md
    assert "Maintain current exposure in CAVA without deploying new capital." in md
    assert "Trim AAA to reduce defensive exposure." in md
    assert "$78.00" in md
    assert "$180.00" in md


def test_daily_snapshot_append_and_idempotency(sample_vault):
    vault_dir = sample_vault["vault_dir"]
    reporter = MarkdownTradeReporter(output_dir=str(vault_dir), append_daily_snapshots=True)

    summary_snap1 = ReconciliationSummary(
        total_portfolio_value=100000.0,
        current_cash=20000.0,
        target_cash_reserve=15000.0,
        projected_ending_cash=20000.0,
        total_buys_dollars=0.0,
        total_sells_dollars=0.0,
        allocations=[],
        clusters={},
        aging_signals=[],
        expired_signals=[],
        all_signals=[],
        max_age_days=14,
        execution_date=date(2026, 9, 11),
        source_file="Portfolio_Positions_Sep-11-2026 (1).csv",
        download_time="Sep-11-2026 3:24 p.m ET",
        execution_timestamp="2026-09-11 15:25:00",
    )

    summary_snap2 = ReconciliationSummary(
        total_portfolio_value=105000.0,
        current_cash=25000.0,
        target_cash_reserve=15000.0,
        projected_ending_cash=25000.0,
        total_buys_dollars=0.0,
        total_sells_dollars=0.0,
        allocations=[],
        clusters={},
        aging_signals=[],
        expired_signals=[],
        all_signals=[],
        max_age_days=14,
        execution_date=date(2026, 9, 11),
        source_file="Portfolio_Positions_Sep-11-2026 (2).csv",
        download_time="Sep-11-2026 3:39 p.m ET",
        execution_timestamp="2026-09-11 15:40:00",
    )

    # 1. First run writes fresh daily file
    report_file = reporter.write_report(summary_snap1)
    content1 = report_file.read_text(encoding="utf-8")
    assert "# Trade Execution Orders — 2026-09-11" in content1
    assert "Portfolio_Positions_Sep-11-2026 (1).csv" in content1
    assert "Sep-11-2026 3:24 p.m ET" in content1
    assert "$100,000.00" in content1

    # 2. Second run on later file appends second snapshot with divider
    report_file = reporter.write_report(summary_snap2)
    content2 = report_file.read_text(encoding="utf-8")
    assert content2.count("# Trade Execution Orders — 2026-09-11") == 1
    assert "\n\n---\n\n" in content2
    assert "Portfolio_Positions_Sep-11-2026 (1).csv" in content2
    assert "Portfolio_Positions_Sep-11-2026 (2).csv" in content2
    assert "Sep-11-2026 3:39 p.m ET" in content2
    assert "$105,000.00" in content2

    # 3. Re-running snap2 does not duplicate the block (idempotency)
    report_file = reporter.write_report(summary_snap2)
    content3 = report_file.read_text(encoding="utf-8")
    assert content3.count("Portfolio_Positions_Sep-11-2026 (2).csv") == 2  # Once in header, once in metadata line
    assert content3.count("\n\n---\n\n") == 1

    # 4. Overwrite resets file to single snapshot
    report_file = reporter.write_report(summary_snap2, overwrite=True)
    content4 = report_file.read_text(encoding="utf-8")
    assert "Portfolio_Positions_Sep-11-2026 (1).csv" not in content4
    assert "Portfolio_Positions_Sep-11-2026 (2).csv" in content4
    assert "\n\n---\n\n" not in content4


