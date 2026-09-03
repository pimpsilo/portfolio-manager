import tempfile
from datetime import date, timedelta
from pathlib import Path
from pm.ingest.broker_csv import BrokerCSVParser
from pm.ingest.markdown_signals import MarkdownSignalParser
from pm.models import SignalType


def test_broker_csv_parsing():
    sample_csv = """Account number,Account name,Symbol,Description,Quantity,Last price,Last price change,Current value,Cost basis total
653586102,BrokerageLink,FDRXX**,HELD IN MONEY MARKET,,,,$50000.00,
653586102,BrokerageLink,AAPL,APPLE INC,15.004,$300.00,+$2.00,$4501.20,$4000.00
653586102,BrokerageLink,MSFT**,MICROSOFT CORP,10,$500.00,+$5.00,$5000.00,$4500.00
653586102,BrokerageLink,Pending Activity,,,,,,
"The data and information in this spreadsheet..."
"""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as f:
        f.write(sample_csv)
        f_path = f.name

    parser = BrokerCSVParser()
    state = parser.parse(f_path)

    assert state.cash_balance == 50000.0
    assert "AAPL" in state.holdings
    assert state.holdings["AAPL"].quantity == 15.004
    assert state.holdings["AAPL"].last_price == 300.0
    assert "MSFT" in state.holdings
    assert state.holdings["MSFT"].symbol == "MSFT"
    assert "Pending Activity" not in state.holdings
    assert state.total_account_value == 50000.0 + 4501.20 + 5000.0


def test_markdown_signal_parsing():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        today = date.today()

        # Active folder
        d_active = tmp_path / f"NVDA_{today.strftime('%Y%m%d')}_120000" / "5_portfolio"
        d_active.mkdir(parents=True)
        (d_active / "decision.md").write_text("**Rating**: Overweight\n**Price Target**: 250.0")

        # Expired folder (20 days ago)
        old_date = today - timedelta(days=20)
        d_old = tmp_path / f"INTC_{old_date.strftime('%Y%m%d')}_120000" / "5_portfolio"
        d_old.mkdir(parents=True)
        (d_old / "decision.md").write_text("**Rating**: Underweight")

        parser = MarkdownSignalParser(tmpdir, max_age_days=14)
        signals = parser.parse_all_signals(as_of_date=today)

        assert "NVDA" in signals
        assert signals["NVDA"].signal == SignalType.OVERWEIGHT
        assert signals["NVDA"].is_expired is False
        assert signals["NVDA"].target_price == 250.0

        assert "INTC" in signals
        assert signals["INTC"].signal == SignalType.UNDERWEIGHT
        assert signals["INTC"].is_expired is True
