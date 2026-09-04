import sys
from pathlib import Path
import numpy as np
import pandas as pd

BASE_DIR = Path('/Users/matthewhope/github_projects/portfolio-manager')
sys.path.insert(0, str(BASE_DIR))

from pm.risk.constraints import PortfolioConstraintOptimizer
from pm.risk.clustering import CorrelationClusterEngine
from pm.models import SignalType

# Load cached prices
prices = pd.read_csv(BASE_DIR / "scratch" / "backtest_prices.csv", index_col=0, parse_dates=True)
universe = [c for c in prices.columns if c != "SPY"]

MEGA_CAPS = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "MA", "COST", "ASML", "NFLX", "AMD", "CRM", "ADBE"}
LARGE_CAPS = {"SPGI", "MCO", "INTU", "BKNG", "UBER", "PDD", "MELI", "CPRT", "TQQQ"}

cluster_engine = CorrelationClusterEngine()
current_optimizer = PortfolioConstraintOptimizer(
    max_position_weight=0.15,
    min_cash_reserve=0.10,
    max_cluster_exposure=0.25,
    multipliers={
        SignalType.OVERWEIGHT: 1.5,
        SignalType.EQUAL_WEIGHT: 1.0,
        SignalType.UNDERWEIGHT: 0.5,
        SignalType.AVOID: 0.0,
    }
)

def run_comparison(start_date_str):
    rebalance_dates = pd.date_range(start=start_date_str, end=prices.index[-1], freq="BME")
    equity_budget = 0.90
    cash_rate_daily = 0.035 / 252

    strat_returns = {
        "Current Engine (As Built in Python)": [],
        "Option 1: Upside / Payoff": [],
        "Option 2: Risk-Parity (Inv Vol)": [],
        "Option 5: Market-Cap Anchor": [],
        "Benchmark: S&P 500 (SPY)": [],
    }
    date_index = []

    w_current = {}
    w_opt1 = {}
    w_opt2 = {}
    w_opt5 = {}

    start_idx = prices.index.get_indexer([pd.to_datetime(start_date_str)], method="bfill")[0]

    for i in range(start_idx, len(prices)):
        current_date = prices.index[i]
        is_rebal = (current_date in rebalance_dates) or (i == start_idx)

        sub_p = prices.iloc[:i]
        available_assets = [t for t in universe if sub_p[t].dropna().count() >= 60]

        if is_rebal and len(available_assets) > 5:
            window = prices.iloc[max(0, i - 126):i][available_assets]
            ret_window = window.pct_change().dropna()
            clusters = cluster_engine.cluster_assets(ret_window)
            vols = (ret_window.std() * np.sqrt(252)).replace(0, np.nan).fillna(0.30)

            # Trailing momentum / technical signals proxy (consistent across models)
            roll_high = window.max()
            last_p = window.iloc[-1]
            drawdown_from_high = (last_p - roll_high) / roll_high
            # Assign signals based on momentum and trend
            sim_signals = {}
            for t in available_assets:
                dd = drawdown_from_high.get(t, -0.10)
                if dd > -0.15:
                    sim_signals[t] = SignalType.OVERWEIGHT   # 1.5x
                elif dd > -0.30:
                    sim_signals[t] = SignalType.EQUAL_WEIGHT # 1.0x
                else:
                    sim_signals[t] = SignalType.UNDERWEIGHT  # 0.5x

            # -------------------------------------------------------------
            # EXACT CURRENT PYTHON ENGINE
            # -------------------------------------------------------------
            w_current = current_optimizer.optimize_weights(
                tickers=available_assets,
                signals=sim_signals,
                clusters=clusters
            )

            # -------------------------------------------------------------
            # OPTION 1: Upside-Weighted
            # -------------------------------------------------------------
            upside_proxy = np.clip((roll_high - last_p) / last_p + 0.10, 0.05, 0.50)
            m_1 = {t: (current_optimizer.multipliers[sim_signals[t]] * (1.0 + upside_proxy[t] * 2.5)) for t in available_assets}
            tot_1 = sum(m_1.values())
            w_1 = {t: min(0.15, (v / tot_1) * equity_budget) for t, v in m_1.items()}
            s1 = sum(w_1.values())
            w_opt1 = {t: (v / s1) * equity_budget for t, v in w_1.items()}

            # -------------------------------------------------------------
            # OPTION 2: Risk-Parity (Inverse Volatility)
            # -------------------------------------------------------------
            m_2 = {t: (current_optimizer.multipliers[sim_signals[t]] * (1.0 / max(0.12, vols[t]))) for t in available_assets}
            tot_2 = sum(m_2.values())
            w_2 = {t: min(0.15, (v / tot_2) * equity_budget) for t, v in m_2.items()}
            s2 = sum(w_2.values())
            w_opt2 = {t: (v / s2) * equity_budget for t, v in w_2.items()}

            # -------------------------------------------------------------
            # OPTION 5: Market-Cap Anchor
            # -------------------------------------------------------------
            m_5 = {}
            for t in available_assets:
                sig_m = current_optimizer.multipliers[sim_signals[t]]
                cap_m = 3.0 if t in MEGA_CAPS else (1.8 if t in LARGE_CAPS else 1.0)
                m_5[t] = cap_m * sig_m
            tot_5 = sum(m_5.values())
            w_5 = {t: min(0.15, (v / tot_5) * equity_budget) for t, v in m_5.items()}
            s5 = sum(w_5.values())
            w_opt5 = {t: (v / s5) * equity_budget for t, v in w_5.items()}

        day_rets = (prices.iloc[i] - prices.iloc[i - 1]) / prices.iloc[i - 1]
        cash_portion = 1.0 - equity_budget

        r_curr = sum(w_current.get(t, 0) * day_rets.get(t, 0) for t in w_current) + (cash_portion * cash_rate_daily)
        r_opt1 = sum(w_opt1.get(t, 0) * day_rets.get(t, 0) for t in w_opt1) + (cash_portion * cash_rate_daily)
        r_opt2 = sum(w_opt2.get(t, 0) * day_rets.get(t, 0) for t in w_opt2) + (cash_portion * cash_rate_daily)
        r_opt5 = sum(w_opt5.get(t, 0) * day_rets.get(t, 0) for t in w_opt5) + (cash_portion * cash_rate_daily)
        r_spy = day_rets.get("SPY", 0.0)

        strat_returns["Current Engine (As Built in Python)"].append(r_curr)
        strat_returns["Option 1: Upside / Payoff"].append(r_opt1)
        strat_returns["Option 2: Risk-Parity (Inv Vol)"].append(r_opt2)
        strat_returns["Option 5: Market-Cap Anchor"].append(r_opt5)
        strat_returns["Benchmark: S&P 500 (SPY)"].append(r_spy)
        date_index.append(current_date)

    returns_df = pd.DataFrame(strat_returns, index=date_index)

    def get_metrics(r_series):
        total_ret = (1 + r_series).prod() - 1
        days = len(r_series)
        cagr = (1 + total_ret) ** (252 / days) - 1
        vol = r_series.std() * np.sqrt(252)
        sharpe = (r_series.mean() * 252 - 0.03) / vol if vol > 0 else 0
        downside = r_series[r_series < 0].std() * np.sqrt(252)
        sortino = (r_series.mean() * 252 - 0.03) / downside if downside > 0 else 0
        cum = (1 + r_series).cumprod()
        dd = (cum - cum.cummax()) / cum.cummax()
        max_dd = dd.min()
        calmar = cagr / abs(max_dd) if abs(max_dd) > 0 else 0
        return {
            "Total Return %": total_ret * 100,
            "CAGR %": cagr * 100,
            "Ann. Vol %": vol * 100,
            "Sharpe": sharpe,
            "Sortino": sortino,
            "Max DD %": max_dd * 100,
            "Calmar": calmar,
        }

    summary = {col: get_metrics(returns_df[col]) for col in returns_df.columns}
    res = pd.DataFrame(summary).T
    return res

print("\n" + "=" * 115)
print("🏆 PERIOD 1: FULL 4.75-YEAR CYCLE (Jan 2022 – Sept 2026)")
print("=" * 115)
print(run_comparison("2022-01-15").round(2).to_string())

print("\n" + "=" * 115)
print("🏆 PERIOD 2: 4-YEAR EXPANSION CYCLE (July 2022 – Sept 2026)")
print("=" * 115)
print(run_comparison("2022-07-01").round(2).to_string())
