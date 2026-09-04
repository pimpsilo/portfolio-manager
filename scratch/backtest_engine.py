import sys
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
import yfinance as yf

BASE_DIR = Path('/Users/matthewhope/github_projects/portfolio-manager')
sys.path.insert(0, str(BASE_DIR))

# Universe of securities in the user's portfolio + candidates
TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "AMD", "NFLX",
    "ASML", "MA", "MCO", "EG", "INTU", "CPRT", "SPGI", "KEYS",
    "BKNG", "WDC", "HIG", "CRM", "DECK", "EFX", "APP", "COST",
    "UBER", "DUOL", "IMAX", "MELI", "PDD", "LULU", "ADBE", "TQQQ", "SPY"
]

# Approximate historical market cap classification (Mega, Large, Mid)
MEGA_CAPS = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META", "MA", "COST", "ASML", "NFLX", "AMD", "CRM", "ADBE"}
LARGE_CAPS = {"SPGI", "MCO", "INTU", "BKNG", "UBER", "PDD", "MELI", "CPRT", "TQQQ"}

def download_data():
    cache_file = BASE_DIR / "scratch" / "backtest_prices.csv"
    if cache_file.exists():
        print("Loading prices from cache...")
        return pd.read_csv(cache_file, index_col=0, parse_dates=True)

    print("Downloading 4-year daily price history via yfinance (2022-2026)...")
    df = yf.download(TICKERS, start="2022-01-01", end="2026-09-02", progress=False)["Close"]
    df = df.ffill().dropna(how="all")
    df.to_csv(cache_file)
    return df

def calculate_metrics(daily_returns, rf_rate=0.03):
    total_days = len(daily_returns)
    if total_days < 20:
        return {}

    total_return = (1 + daily_returns).prod() - 1
    cagr = (1 + total_return) ** (252 / total_days) - 1
    ann_vol = daily_returns.std() * np.sqrt(252)

    # Sharpe
    excess_ret = daily_returns.mean() * 252 - rf_rate
    sharpe = excess_ret / ann_vol if ann_vol > 0 else 0.0

    # Sortino
    downside = daily_returns[daily_returns < 0].std() * np.sqrt(252)
    sortino = excess_ret / downside if downside > 0 else 0.0

    # Max Drawdown
    cum_returns = (1 + daily_returns).cumprod()
    peak = cum_returns.cummax()
    drawdown = (cum_returns - peak) / peak
    max_dd = drawdown.min()

    # Calmar Ratio
    calmar = cagr / abs(max_dd) if abs(max_dd) > 0 else 0.0

    return {
        "Total Return %": total_return * 100,
        "CAGR %": cagr * 100,
        "Annualized Vol %": ann_vol * 100,
        "Sharpe Ratio": sharpe,
        "Sortino Ratio": sortino,
        "Max Drawdown %": max_dd * 100,
        "Calmar Ratio": calmar,
    }

def run_backtest():
    prices = download_data()
    universe = [c for c in prices.columns if c != "SPY"]
    rebalance_dates = pd.date_range(start="2022-01-15", end=prices.index[-1], freq="BME")

    equity_budget = 0.90  # 10% safe cash reserve
    cash_rate_daily = 0.035 / 252  # 3.5% annualized interest on cash

    # Track daily returns for each strategy
    strat_returns = {
        "Baseline (Equal Weight)": [],
        "Option 1: Upside / Payoff": [],
        "Option 2: Risk-Parity (Inv Vol)": [],
        "Option 5: Market-Cap Anchor": [],
        "Benchmark: S&P 500 (SPY)": [],
    }
    date_index = []

    # Current active weights for each strategy
    weights_base = {}
    weights_opt1 = {}
    weights_opt2 = {}
    weights_opt5 = {}

    for i in range(1, len(prices)):
        current_date = prices.index[i]
        prev_date = prices.index[i - 1]

        # Check if today is a rebalance day
        is_rebal = (current_date in rebalance_dates) or (i == 1)

        # Assets available on this date (at least 60 days of history)
        sub_p = prices.iloc[:i]
        available_assets = [t for t in universe if sub_p[t].dropna().count() >= 60]

        if is_rebal and len(available_assets) > 5:
            # 6-month historical window for stats
            window = prices.iloc[max(0, i - 126):i][available_assets]
            ret_window = window.pct_change().dropna()
            vols = ret_window.std() * np.sqrt(252)
            vols = vols.replace(0, np.nan).fillna(vols.median())

            # Trailing momentum / upside proxy (distance to 52w high + 12m return)
            roll_high = window.max()
            last_p = window.iloc[-1]
            upside_proxy = np.clip((roll_high - last_p) / last_p + 0.10, 0.05, 0.50)

            # ---------------------------------------------------------
            # Strategy 0: Baseline Equal Weight (1/N)
            # ---------------------------------------------------------
            w_b = {t: equity_budget / len(available_assets) for t in available_assets}
            weights_base = w_b

            # ---------------------------------------------------------
            # Strategy 1: Option 1 (Upside / Payoff Sizing)
            # ---------------------------------------------------------
            m_1 = {t: (1.0 + upside_proxy[t] * 2.5) for t in available_assets}
            tot_1 = sum(m_1.values())
            w_1 = {t: min(0.15, (v / tot_1) * equity_budget) for t, v in m_1.items()}
            # Re-normalize
            s1 = sum(w_1.values())
            weights_opt1 = {t: (v / s1) * equity_budget for t, v in w_1.items()}

            # ---------------------------------------------------------
            # Strategy 2: Option 2 (Inverse Volatility / Risk-Parity)
            # ---------------------------------------------------------
            m_2 = {t: (1.0 / max(0.12, vols[t])) for t in available_assets}
            tot_2 = sum(m_2.values())
            w_2 = {t: min(0.15, (v / tot_2) * equity_budget) for t, v in m_2.items()}
            s2 = sum(w_2.values())
            weights_opt2 = {t: (v / s2) * equity_budget for t, v in w_2.items()}

            # ---------------------------------------------------------
            # Strategy 5: Option 5 (Market-Cap / Maturity Anchor)
            # ---------------------------------------------------------
            m_5 = {}
            for t in available_assets:
                if t in MEGA_CAPS:
                    m_5[t] = 3.0
                elif t in LARGE_CAPS:
                    m_5[t] = 1.8
                else:
                    m_5[t] = 1.0
            tot_5 = sum(m_5.values())
            w_5 = {t: min(0.15, (v / tot_5) * equity_budget) for t, v in m_5.items()}
            s5 = sum(w_5.values())
            weights_opt5 = {t: (v / s5) * equity_budget for t, v in w_5.items()}

        # Compute daily asset returns
        day_rets = (prices.iloc[i] - prices.iloc[i - 1]) / prices.iloc[i - 1]

        # Calculate daily portfolio return for each strategy: (Equity Return) + (Cash Return)
        cash_portion = 1.0 - equity_budget

        r_base = sum(weights_base.get(t, 0) * day_rets.get(t, 0) for t in weights_base) + (cash_portion * cash_rate_daily)
        r_opt1 = sum(weights_opt1.get(t, 0) * day_rets.get(t, 0) for t in weights_opt1) + (cash_portion * cash_rate_daily)
        r_opt2 = sum(weights_opt2.get(t, 0) * day_rets.get(t, 0) for t in weights_opt2) + (cash_portion * cash_rate_daily)
        r_opt5 = sum(weights_opt5.get(t, 0) * day_rets.get(t, 0) for t in weights_opt5) + (cash_portion * cash_rate_daily)

        r_spy = day_rets.get("SPY", 0.0)

        strat_returns["Baseline (Equal Weight)"].append(r_base)
        strat_returns["Option 1: Upside / Payoff"].append(r_opt1)
        strat_returns["Option 2: Risk-Parity (Inv Vol)"].append(r_opt2)
        strat_returns["Option 5: Market-Cap Anchor"].append(r_opt5)
        strat_returns["Benchmark: S&P 500 (SPY)"].append(r_spy)
        date_index.append(current_date)

    # Convert to DataFrame
    returns_df = pd.DataFrame(strat_returns, index=date_index)

    # Calculate metrics for each
    metrics_summary = {}
    for col in returns_df.columns:
        metrics_summary[col] = calculate_metrics(returns_df[col])

    results_table = pd.DataFrame(metrics_summary).T
    print("\n" + "=" * 115)
    print(f"🏆 4-YEAR HISTORICAL BACKTEST RESULTS (Jan 2022 – Sept 2026)")
    print("=" * 115)
    print(results_table.round(2).to_string())
    print("=" * 115)

if __name__ == "__main__":
    run_backtest()
