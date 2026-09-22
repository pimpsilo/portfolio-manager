import pandas as pd

from pm.backtest import BacktestConfig, evaluate, run_parameter_sweep


def test_backtest_returns_comparable_metrics():
    prices = pd.DataFrame(
        {
            "AAPL": [100 + i for i in range(40)],
            "MSFT": [100 + 2 * i for i in range(40)],
        },
        index=pd.date_range("2025-01-01", periods=40, freq="B"),
    )

    result = evaluate(prices, BacktestConfig())

    assert result["total_return_pct"] > 0
    assert result["rebalance_count"] >= 1
    assert 0 <= result["average_equity_weight"] <= 1


def test_parameter_sweep_has_explicit_variants():
    prices = pd.DataFrame(
        {"AAPL": [100 + i for i in range(40)], "MSFT": [100 + i for i in range(40)]},
        index=pd.date_range("2025-01-01", periods=40, freq="B"),
    )

    results = run_parameter_sweep(prices, BacktestConfig())

    assert len(results) == 9
    assert {"name", "sharpe", "max_drawdown_pct", "turnover"} <= set(results.columns)
