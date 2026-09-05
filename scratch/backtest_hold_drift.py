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
optimizer = PortfolioConstraintOptimizer(
    max_position_weight=0.15,
    min_cash_reserve=0.15,       # 15% cash reserve
    max_cluster_exposure=0.25,
    multipliers={
        SignalType.OVERWEIGHT: 1.5,
        SignalType.EQUAL_WEIGHT: 1.0,
        SignalType.UNDERWEIGHT: 0.5,
        SignalType.AVOID: 0.0,
    },
    market_cap_cfg={
        "enabled": True,
        "mega_cap_threshold": 200_000_000_000,
        "large_cap_threshold": 50_000_000_000,
        "tier_multipliers": {
            "mega_cap": 3.0,
            "large_cap": 1.8,
            "mid_cap": 1.0,
        }
    }
)

def run_simulation(start_date_str, hold_drift_tolerance=1.00):
    start_idx = prices.index.get_indexer([pd.to_datetime(start_date_str)], method="bfill")[0]
    dates = prices.index[start_idx:]
    
    # Bi-weekly rebalancing checks (every 10 business days)
    rebalance_dates = set(dates[::10])
    cash_rate_daily = 0.035 / 252

    # Models to compare:
    # 1. Option 5 with Symmetric 20% Drift (Current Engine)
    # 2. Option 5 with Asymmetric Hold Drift (Proposed: 100% tolerance on Holds)
    # 3. Option 5 Perfect Rebalance (0% drift filter, rebalance every period)
    # 4. S&P 500 Benchmark (SPY)
    
    strategies = ["Symmetric 20% Drift (Current)", "Asymmetric Hold Drift (Proposed)", "Pure Rebalance (0% Filter)"]
    
    portfolio_val = {s: 100_000.0 for s in strategies}
    cash = {s: 15_000.0 for s in strategies} # 15% initial cash
    shares = {s: {t: 0.0 for t in universe} for s in strategies}
    
    # Track daily portfolio value
    history_val = {s: [] for s in strategies}
    history_val["SPY Benchmark"] = []
    turnover_dollars = {s: 0.0 for s in strategies}
    trades_count = {s: 0 for s in strategies}
    
    initial_spy_price = prices["SPY"].iloc[start_idx]
    
    # Initial allocation at start_idx
    init_assets = [t for t in universe if prices[t].iloc[:start_idx].dropna().count() >= 60]
    window = prices.iloc[max(0, start_idx - 126):start_idx][init_assets]
    ret_window = window.pct_change().dropna()
    clusters = cluster_engine.cluster_assets(ret_window)
    
    # Proxy signals based on trend & drawdown
    roll_high = window.max()
    last_p = window.iloc[-1]
    dd = (last_p - roll_high) / roll_high
    init_signals = {}
    for t in init_assets:
        if dd.get(t, -0.1) > -0.12:
            init_signals[t] = SignalType.OVERWEIGHT
        elif dd.get(t, -0.1) > -0.28:
            init_signals[t] = SignalType.EQUAL_WEIGHT
        else:
            init_signals[t] = SignalType.UNDERWEIGHT
            
    # Mock market caps
    mcaps = {t: 300e9 if t in MEGA_CAPS else (100e9 if t in LARGE_CAPS else 20e9) for t in init_assets}
    init_weights = optimizer.optimize_weights(init_assets, init_signals, clusters, mcaps)
    
    for s in strategies:
        equity_to_deploy = 85_000.0
        for t, w in init_weights.items():
            alloc_dollars = (w / 0.85) * equity_to_deploy
            p = prices[t].iloc[start_idx]
            if p > 0:
                sh = np.floor(alloc_dollars / p)
                shares[s][t] = sh
                spent = sh * p
                cash[s] -= (spent - alloc_dollars) # reconcile leftover to cash
                turnover_dollars[s] += spent
                trades_count[s] += 1
                
    for i in range(start_idx, len(prices)):
        current_date = prices.index[i]
        curr_prices = prices.iloc[i]
        
        # 1. Calculate daily valuations
        for s in strategies:
            equity_val = sum(shares[s][t] * curr_prices[t] for t in universe if shares[s][t] > 0)
            cash[s] *= (1 + cash_rate_daily)
            tot = equity_val + cash[s]
            portfolio_val[s] = tot
            history_val[s].append(tot)
            
        history_val["SPY Benchmark"].append(100_000.0 * (curr_prices["SPY"] / initial_spy_price))
        
        # 2. Rebalance check
        is_rebal = (current_date in rebalance_dates) and (i > start_idx)
        if is_rebal:
            sub_p = prices.iloc[:i]
            avail = [t for t in universe if sub_p[t].dropna().count() >= 60]
            w_win = prices.iloc[max(0, i - 126):i][avail]
            r_win = w_win.pct_change().dropna()
            cl = cluster_engine.cluster_assets(r_win)
            
            r_high = w_win.max()
            l_p = w_win.iloc[-1]
            drawdown = (l_p - r_high) / r_high
            sigs = {}
            for t in avail:
                d = drawdown.get(t, -0.10)
                if d > -0.12:
                    sigs[t] = SignalType.OVERWEIGHT
                elif d > -0.28:
                    sigs[t] = SignalType.EQUAL_WEIGHT
                else:
                    sigs[t] = SignalType.UNDERWEIGHT
                    
            cur_mcaps = {t: 300e9 if t in MEGA_CAPS else (100e9 if t in LARGE_CAPS else 20e9) for t in avail}
            target_weights = optimizer.optimize_weights(avail, sigs, cl, cur_mcaps)
            
            for s in strategies:
                tot_val = portfolio_val[s]
                for t in avail:
                    p = curr_prices[t]
                    if p <= 0:
                        continue
                    cur_sh = shares[s][t]
                    cur_pos_val = cur_sh * p
                    tgt_val = target_weights.get(t, 0.0) * tot_val
                    delta_d = tgt_val - cur_pos_val
                    sig = sigs.get(t, SignalType.EQUAL_WEIGHT)
                    
                    # Rebalance decision logic
                    should_trade = False
                    order_shares = 0
                    
                    if s == "Pure Rebalance (0% Filter)":
                        order_shares = np.round(delta_d / p)
                        should_trade = abs(delta_d) >= 500.0 and abs(order_shares) > 0
                    elif s == "Symmetric 20% Drift (Current)":
                        if abs(delta_d) >= 1500.0:
                            rel_d = abs(delta_d) / tgt_val if tgt_val > 0 else 1.0
                            if rel_d > 0.20:
                                order_shares = np.round(delta_d / p)
                                should_trade = abs(order_shares) > 0
                    elif s == "Asymmetric Hold Drift (Proposed)":
                        # Asymmetric rule:
                        if abs(delta_d) >= 1500.0:
                            rel_d = abs(delta_d) / tgt_val if tgt_val > 0 else 1.0
                            if delta_d < 0 and sig == SignalType.EQUAL_WEIGHT:
                                # Over target, but signal is HOLD
                                # Cap single position at 15%
                                if cur_pos_val / tot_val > 0.15:
                                    excess = cur_pos_val - (0.15 * tot_val)
                                    order_shares = -np.floor(excess / p)
                                    should_trade = abs(order_shares) > 0
                                elif rel_d > hold_drift_tolerance:
                                    # Beyond hold tolerance (e.g. >100% drift)
                                    order_shares = np.round(delta_d / p)
                                    should_trade = abs(order_shares) > 0
                                else:
                                    # SUPPRESSED / PROTECTED HOLD!
                                    should_trade = False
                            else:
                                if rel_d > 0.20:
                                    order_shares = np.round(delta_d / p)
                                    should_trade = abs(order_shares) > 0
                                    
                    if should_trade and order_shares != 0:
                        trade_cost = order_shares * p
                        # Sizing guard: cannot sell more than held
                        if order_shares < 0 and abs(order_shares) > cur_sh:
                            order_shares = -cur_sh
                            trade_cost = order_shares * p
                        # Cash guard: cannot buy more than available cash (less min reserve)
                        if order_shares > 0 and trade_cost > (cash[s] - 0.10 * tot_val):
                            trade_cost = max(0, cash[s] - 0.10 * tot_val)
                            order_shares = np.floor(trade_cost / p)
                            trade_cost = order_shares * p
                            
                        if order_shares != 0:
                            shares[s][t] += order_shares
                            cash[s] -= trade_cost
                            turnover_dollars[s] += abs(trade_cost)
                            trades_count[s] += 1

    # Compute metrics
    df_res = pd.DataFrame(history_val, index=dates)
    daily_rets = df_res.pct_change().dropna()
    
    summary = {}
    years = len(daily_rets) / 252.0
    
    for col in df_res.columns:
        r = daily_rets[col]
        tot_ret = (df_res[col].iloc[-1] / df_res[col].iloc[0]) - 1.0
        cagr = (1.0 + tot_ret) ** (1.0 / years) - 1.0
        vol = r.std() * np.sqrt(252)
        rf = 0.03
        sharpe = (r.mean() * 252 - rf) / vol if vol > 0 else 0
        downside = r[r < 0].std() * np.sqrt(252)
        sortino = (r.mean() * 252 - rf) / downside if downside > 0 else 0
        cum = df_res[col]
        dd = (cum - cum.cummax()) / cum.cummax()
        max_dd = dd.min()
        calmar = cagr / abs(max_dd) if abs(max_dd) > 0 else 0
        
        ann_turnover = (turnover_dollars.get(col, 0.0) / 100_000.0) / years if col in turnover_dollars else 0.0
        n_trades = trades_count.get(col, 0)
        
        summary[col] = {
            "Total Return %": tot_ret * 100,
            "CAGR %": cagr * 100,
            "Ann. Vol %": vol * 100,
            "Sharpe": sharpe,
            "Sortino": sortino,
            "Max DD %": max_dd * 100,
            "Calmar": calmar,
            "Ann. Turnover %": ann_turnover * 100,
            "Total Trades": n_trades,
        }
        
    return pd.DataFrame(summary).T

print("\n" + "=" * 115)
print("🏆 PERIOD 1: FULL 4.75-YEAR CYCLE (Jan 2022 – Sept 2026)")
print("=" * 115)
res1 = run_simulation("2022-01-15", hold_drift_tolerance=1.00)
print(res1.round(2).to_string())

print("\n" + "=" * 115)
print("🏆 PERIOD 2: 4-YEAR EXPANSION CYCLE (July 2022 – Sept 2026)")
print("=" * 115)
res2 = run_simulation("2022-07-01", hold_drift_tolerance=1.00)
print(res2.round(2).to_string())
