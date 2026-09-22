"""Deterministic historical parameter evaluation for portfolio allocation settings."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd

from pm.models import SignalType
from pm.risk.constraints import PortfolioConstraintOptimizer


@dataclass(frozen=True)
class BacktestConfig:
    name: str = "current"
    min_cash_reserve: float = 0.15
    max_position_weight: float = 0.15
    max_cluster_exposure: float = 0.25
    rebalance_months: int = 1
    cash_annual_rate: float = 0.035


def load_prices(path: str) -> pd.DataFrame:
    prices = pd.read_csv(path, index_col=0, parse_dates=True)
    prices = prices.apply(pd.to_numeric, errors="coerce").sort_index()
    prices = prices.dropna(axis=1, how="all").ffill().dropna(how="all")
    if prices.empty or len(prices) < 20:
        raise ValueError("Price history must contain at least 20 usable rows.")
    return prices


def evaluate(prices: pd.DataFrame, config: BacktestConfig) -> Dict[str, float]:
    """Evaluate monthly constrained allocation and report comparable metrics."""
    tickers = list(prices.columns)
    signals = {ticker: SignalType.EQUAL_WEIGHT for ticker in tickers}
    caps = {ticker: 35e9 for ticker in tickers}
    optimizer = PortfolioConstraintOptimizer(
        max_position_weight=config.max_position_weight,
        min_cash_reserve=config.min_cash_reserve,
        max_cluster_exposure=config.max_cluster_exposure,
    )
    weights: Dict[str, float] = {}
    daily_returns: List[float] = []
    turnover = 0.0
    rebalance_count = 0
    previous_value = 1.0

    for index in range(1, len(prices)):
        current = prices.iloc[index]
        previous = prices.iloc[index - 1]
        is_rebalance = index == 1 or (
            prices.index[index].month != prices.index[index - 1].month
            and prices.index[index].month % config.rebalance_months == 0
        )
        if is_rebalance:
            clusters = {position + 1: [ticker] for position, ticker in enumerate(tickers)}
            new_weights = optimizer.optimize_weights(tickers, signals, clusters, caps)
            turnover += sum(abs(new_weights.get(t, 0.0) - weights.get(t, 0.0)) for t in tickers)
            weights = new_weights
            rebalance_count += 1

        asset_returns = (current / previous - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        cash_weight = 1.0 - sum(weights.values())
        portfolio_return = sum(weights.get(t, 0.0) * asset_returns[t] for t in tickers)
        portfolio_return += cash_weight * config.cash_annual_rate / 252
        daily_returns.append(float(portfolio_return))
        previous_value *= 1.0 + portfolio_return

    returns = pd.Series(daily_returns, index=prices.index[1:])
    cumulative = (1.0 + returns).cumprod()
    drawdown = cumulative / cumulative.cummax() - 1.0
    volatility = returns.std() * np.sqrt(252)
    annualized_return = previous_value ** (252 / max(len(returns), 1)) - 1.0
    downside = returns[returns < 0].std() * np.sqrt(252)
    return {
        "name": config.name,
        "total_return_pct": round((previous_value - 1.0) * 100, 4),
        "annualized_return_pct": round(annualized_return * 100, 4),
        "annualized_volatility_pct": round(volatility * 100, 4),
        "sharpe": round((returns.mean() * 252 - 0.0) / volatility, 4) if volatility > 0 else 0.0,
        "sortino": round((returns.mean() * 252) / downside, 4) if downside > 0 else 0.0,
        "max_drawdown_pct": round(drawdown.min() * 100, 4),
        "turnover": round(turnover, 4),
        "rebalance_count": float(rebalance_count),
        "average_equity_weight": round(1.0 - config.min_cash_reserve, 4),
    }


def parameter_grid(base: BacktestConfig) -> Iterable[BacktestConfig]:
    """Return a small, explicit grid around current production parameters."""
    for cash in (max(0.0, base.min_cash_reserve - 0.05), base.min_cash_reserve, min(0.95, base.min_cash_reserve + 0.05)):
        for position_cap in (0.10, base.max_position_weight, 0.20):
            yield replace(
                base,
                name=f"cash={cash:.0%},position={position_cap:.0%}",
                min_cash_reserve=cash,
                max_position_weight=position_cap,
            )


def run_parameter_sweep(prices: pd.DataFrame, base: BacktestConfig) -> pd.DataFrame:
    results = [evaluate(prices, config) for config in parameter_grid(base)]
    return pd.DataFrame(results).sort_values(
        ["sharpe", "max_drawdown_pct"], ascending=[False, False]
    )


def write_results(results: pd.DataFrame, output: str) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(path, index=False)
    return path
