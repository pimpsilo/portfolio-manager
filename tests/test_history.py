import csv
from datetime import date, datetime
from pathlib import Path

import pytest

from pm.engine.history import TransactionHistoryTracker, TransactionRecord


def test_scan_vault_reports(tmp_path: Path):
    vault_dir = tmp_path / "00_trade_orders"
    vault_dir.mkdir(parents=True)

    # 1. Report from 2026-10-17 with BUY AAPL and SELL MSFT
    report_1 = vault_dir / "Trade_Orders_2026-10-17.md"
    report_1.write_text(
        """# Trade Execution Orders — 2026-10-17

## 🎯 1. Immediate / Daily Actions

| Ticker | Current Shares | Current Price | Target Weight | Target Value | Delta ($) | Action | Order Shares | Directive Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **[AAPL](../01_agent_reports/AAPL/AAPL_20261017_100000/complete_report.md)**<br><span>2026-10-17</span> | 20 | $200.00 | 5.0% | $10,000.00 | $+6,000.00 | **BUY** 🟢 | **15** | REBALANCE_BUY_STEP |
| **MSFT** | 40 | $400.00 | 5.0% | $10,000.00 | $-6,000.00 | **SELL** 🔴 | **10** | REBALANCE_TRIM_STEP |
| **GOOG** | 50 | $150.00 | 5.0% | $7,500.00 | $0.00 | **HOLD** 🟡 | **0** | ALIGNED |
""",
        encoding="utf-8",
    )

    # 2. Report from 2026-10-21 with BUY AAPL (more recent than Oct 17)
    report_2 = vault_dir / "Trade_Orders_2026-10-21.md"
    report_2.write_text(
        """# Trade Execution Orders — 2026-10-21

## 🎯 1. Immediate / Daily Actions

| Ticker | Current Shares | Current Price | Target Weight | Target Value | Delta ($) | Action | Order Shares | Directive Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **[AAPL](complete_report.md)** | 35 | $210.00 | 8.0% | $16,000.00 | $+8,000.00 | **BUY** 🟢 | **20** | REBALANCE_BUY_STEP |
""",
        encoding="utf-8",
    )

    tracker = TransactionHistoryTracker(obsidian_vault_dir=str(vault_dir), lookback_days=30)
    as_of = date(2026, 10, 23)

    records = tracker.scan_vault_reports(as_of_date=as_of)

    assert "AAPL" in records
    assert records["AAPL"].trade_date == date(2026, 10, 21)
    assert records["AAPL"].action == "BUY"
    assert records["AAPL"].shares == 20.0
    assert records["AAPL"].source == "VAULT_REPORT"

    assert "MSFT" in records
    assert records["MSFT"].trade_date == date(2026, 10, 17)
    assert records["MSFT"].action == "SELL"
    assert records["MSFT"].shares == 10.0

    # GOOG had order_shares = 0 (HOLD), so it should not be recorded as a transaction
    assert "GOOG" not in records


def test_scan_broker_csv_quantity_changes(tmp_path: Path):
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True)

    header = [
        "Account number", "Account name", "Symbol", "Description", "Quantity",
        "Last price", "Last price change", "Current value", "Today's gain/loss dollar",
        "Today's gain/loss percent", "Total gain/loss dollar", "Total gain/loss percent",
        "Percent of account", "Cost basis total", "Average cost basis", "Type",
    ]

    # Day 1: Oct 19 - AMD has 32 shares, AMZN has 88 shares
    csv_1 = downloads_dir / "Portfolio_Positions_Oct-19-2026.csv"
    with open(csv_1, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerow(["123", "BROKERAGELINK", "FDRXX**", "CASH", "", "", "", "$10000.00"])
        writer.writerow(["123", "BROKERAGELINK", "AMD", "ADVANCED MICRO", "32", "$200.00", "", "$6400.00"])
        writer.writerow(["123", "BROKERAGELINK", "AMZN", "AMAZON COM", "88", "$180.00", "", "$15840.00"])
        writer.writerow(["Date downloaded Oct-19-2026 10:00 a.m. ET"])

    # Day 2: Oct 21 - AMD changed from 32 to 42 (+10 BUY), AMZN stayed 88
    csv_2 = downloads_dir / "Portfolio_Positions_Oct-21-2026.csv"
    with open(csv_2, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerow(["123", "BROKERAGELINK", "FDRXX**", "CASH", "", "", "", "$8000.00"])
        writer.writerow(["123", "BROKERAGELINK", "AMD", "ADVANCED MICRO", "42", "$205.00", "", "$8610.00"])
        writer.writerow(["123", "BROKERAGELINK", "AMZN", "AMAZON COM", "88", "$185.00", "", "$16280.00"])
        writer.writerow(["Date downloaded Oct-21-2026 10:00 a.m. ET"])

    # Day 3: Oct 22 - AMD changed from 42 to 48 (+6 BUY), AMZN decreased from 88 to 78 (-10 SELL)
    csv_3 = downloads_dir / "Portfolio_Positions_Oct-22-2026.csv"
    with open(csv_3, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerow(["123", "BROKERAGELINK", "FDRXX**", "CASH", "", "", "", "$8500.00"])
        writer.writerow(["123", "BROKERAGELINK", "AMD", "ADVANCED MICRO", "48", "$210.00", "", "$10080.00"])
        writer.writerow(["123", "BROKERAGELINK", "AMZN", "AMAZON COM", "78", "$190.00", "", "$14820.00"])
        writer.writerow(["Date downloaded Oct-22-2026 10:00 a.m. ET"])

    tracker = TransactionHistoryTracker(downloads_dir=str(downloads_dir), lookback_days=30)
    as_of = date(2026, 10, 23)

    records = tracker.scan_broker_csv_quantity_changes(as_of_date=as_of)

    # AMD changed most recently on Oct 22 (+6)
    assert "AMD" in records
    assert records["AMD"].trade_date == date(2026, 10, 22)
    assert records["AMD"].action == "BUY"
    assert records["AMD"].shares == 6.0
    assert records["AMD"].source == "BROKER_CSV"

    # AMZN sold 10 shares on Oct 22
    assert "AMZN" in records
    assert records["AMZN"].trade_date == date(2026, 10, 22)
    assert records["AMZN"].action == "SELL"
    assert records["AMZN"].shares == 10.0


def test_get_last_transactions_hybrid_merge_and_aliasing(tmp_path: Path):
    vault_dir = tmp_path / "00_trade_orders"
    vault_dir.mkdir(parents=True)
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True)

    # Vault report shows GOOG bought on Oct 20
    report = vault_dir / "Trade_Orders_2026-10-20.md"
    report.write_text(
        """# Trade Execution Orders — 2026-10-20

## 🎯 1. Immediate / Daily Actions

| Ticker | Current Shares | Current Price | Target Weight | Target Value | Delta ($) | Action | Order Shares | Directive Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **GOOG** | 0 | $150.00 | 5.0% | $7,500.00 | $+7,500.00 | **BUY** 🟢 | **50** | NEW_STARTER_ENTRY |
""",
        encoding="utf-8",
    )

    # Broker CSV shows NVDA changed on Oct 19
    header = [
        "Account number", "Account name", "Symbol", "Description", "Quantity",
        "Last price", "Last price change", "Current value", "Today's gain/loss dollar",
        "Today's gain/loss percent", "Total gain/loss dollar", "Total gain/loss percent",
        "Percent of account", "Cost basis total", "Average cost basis", "Type",
    ]
    csv_1 = downloads_dir / "Portfolio_Positions_Oct-18-2026.csv"
    with open(csv_1, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerow(["123", "BROKERAGELINK", "FDRXX**", "CASH", "", "", "", "$10000.00"])
        writer.writerow(["123", "BROKERAGELINK", "NVDA", "NVIDIA", "10", "$100.00", "", "$1000.00"])
        writer.writerow(["Date downloaded Oct-18-2026 10:00 a.m. ET"])

    csv_2 = downloads_dir / "Portfolio_Positions_Oct-19-2026.csv"
    with open(csv_2, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerow(["123", "BROKERAGELINK", "FDRXX**", "CASH", "", "", "", "$8000.00"])
        writer.writerow(["123", "BROKERAGELINK", "NVDA", "NVIDIA", "20", "$100.00", "", "$2000.00"])
        writer.writerow(["Date downloaded Oct-19-2026 10:00 a.m. ET"])

    tracker = TransactionHistoryTracker(
        obsidian_vault_dir=str(vault_dir),
        downloads_dir=str(downloads_dir),
        equivalent_symbols=[["GOOG", "GOOGL"]],
    )
    as_of = date(2026, 10, 23)

    last_txs = tracker.get_last_transactions(as_of_date=as_of)

    # GOOG recorded from vault on Oct 20
    assert "GOOG" in last_txs
    assert last_txs["GOOG"].trade_date == date(2026, 10, 20)

    # Dual-class aliasing: GOOGL also inherits Oct 20 transaction!
    assert "GOOGL" in last_txs
    assert last_txs["GOOGL"].trade_date == date(2026, 10, 20)

    # NVDA recorded from CSV on Oct 19
    assert "NVDA" in last_txs
    assert last_txs["NVDA"].trade_date == date(2026, 10, 19)


def test_get_cooldown_status():
    tracker = TransactionHistoryTracker()
    as_of = date(2026, 10, 23)

    last_txs = {
        # Traded 2 days ago (Oct 21) -> In 7-day cooldown (5 days remaining)
        "RECENT": TransactionRecord("RECENT", date(2026, 10, 21), "BUY", 10.0, "BROKER_CSV"),
        # Traded 6 days ago (Oct 17) -> In 7-day cooldown (1 day remaining)
        "EXPIRING_SOON": TransactionRecord("EXPIRING_SOON", date(2026, 10, 17), "SELL", 5.0, "VAULT_REPORT"),
        # Traded exactly 7 days ago (Oct 16) -> Cooldown elapsed! Eligible to trade.
        "ELIGIBLE_7D": TransactionRecord("ELIGIBLE_7D", date(2026, 10, 16), "BUY", 10.0, "VAULT_REPORT"),
        # Traded 10 days ago (Oct 13) -> Cooldown elapsed! Eligible to trade.
        "OLD": TransactionRecord("OLD", date(2026, 10, 13), "BUY", 20.0, "BROKER_CSV"),
    }

    # Case 1: RECENT (2d ago) -> Cooldown active
    status_recent = tracker.get_cooldown_status("RECENT", as_of_date=as_of, cooldown_days=7, last_transactions=last_txs)
    assert status_recent is not None
    last_dt, days_since, days_remaining = status_recent
    assert last_dt == date(2026, 10, 21)
    assert days_since == 2
    assert days_remaining == 5

    # Case 2: EXPIRING_SOON (6d ago) -> Cooldown active, 1d remaining
    status_expiring = tracker.get_cooldown_status("EXPIRING_SOON", as_of_date=as_of, cooldown_days=7, last_transactions=last_txs)
    assert status_expiring is not None
    _, days_since_exp, days_rem_exp = status_expiring
    assert days_since_exp == 6
    assert days_rem_exp == 1

    # Case 3: ELIGIBLE_7D (7d ago) -> None (eligible!)
    status_7d = tracker.get_cooldown_status("ELIGIBLE_7D", as_of_date=as_of, cooldown_days=7, last_transactions=last_txs)
    assert status_7d is None

    # Case 4: OLD (10d ago) -> None (eligible!)
    status_old = tracker.get_cooldown_status("OLD", as_of_date=as_of, cooldown_days=7, last_transactions=last_txs)
    assert status_old is None

    # Case 5: Untraded ticker -> None (eligible!)
    status_untraded = tracker.get_cooldown_status("NEWTICKER", as_of_date=as_of, cooldown_days=7, last_transactions=last_txs)
    assert status_untraded is None

    # Case 6: cooldown_days = 0 (disabled) -> None
    status_disabled = tracker.get_cooldown_status("RECENT", as_of_date=as_of, cooldown_days=0, last_transactions=last_txs)
    assert status_disabled is None
