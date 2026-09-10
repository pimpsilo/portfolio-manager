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
