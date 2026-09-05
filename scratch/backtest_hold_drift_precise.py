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
    
    # Bi-weekly rebalancing checks (every 10 trading days)
    rebalance_dates = set(dates[::10])
    cash_rate_daily = 0.035 / 252
    equity_budget = 0.85 # 15% cash

    strategies = [
        "Symmetric 20% Drift (Current Engine)",
        "Asymmetric Hold Drift (Proposed)",
        "Pure Rebalance (0% Drift Filter)",
    ]

    # Current weights for each strategy
    # map ticker -> weight in total portfolio (equity sum <= 0.85)
    weights = {s: {} for s in strategies}
    cash_weight = {s: 0.15 for s in strategies}
    
    strat_returns = {s: [] for s in strategies}
    strat_returns["S&P 500 (SPY)"] = []
    
    turnover = {s: 0.0 for s in strategies}
    num_trades = {s: 0 for s in strategies}
    trades_by_action = {s: {"BUY": 0, "SELL": 0} for s in strategies}
    
    date_index = []

    for i in range(start_idx, len(prices)):
        current_date = prices.index[i]
        prev_date = prices.index[i - 1]
        day_rets = (prices.iloc[i] - prices.iloc[i - 1]) / prices.iloc[i - 1]
        
        is_rebal = (current_date in rebalance_dates) or (i == start_idx)
        
        sub_p = prices.iloc[:i]
        avail = [t for t in universe if sub_p[t].dropna().count() >= 60]
        
        # 1. On rebalance days, evaluate signals & targets
        if is_rebal and len(avail) > 5:
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
                cur_w = weights[s]
                
                # First day initialization
                if i == start_idx:
                    weights[s] = {t: target_weights.get(t, 0.0) for t in avail}
                    turnover[s] += sum(weights[s].values())
                    num_trades[s] += len(weights[s])
                    continue
                    
                new_w = dict(cur_w)
                
                for t in avail:
                    target_w = target_weights.get(t, 0.0)
                    cur_pos_w = cur_w.get(t, 0.0)
                    delta_w = target_w - cur_pos_w
                    sig = sigs.get(t, SignalType.EQUAL_WEIGHT)
                    
                    if target_w <= 0.0 and cur_pos_w > 0:
                        # Full exit
                        new_w[t] = 0.0
                        turnover[s] += cur_pos_w
                        num_trades[s] += 1
                        trades_by_action[s]["SELL"] += 1
                        continue
                        
                    if cur_pos_w <= 0 and target_w > 0:
                        # New position starter
                        new_w[t] = target_w
                        turnover[s] += target_w
                        num_trades[s] += 1
                        trades_by_action[s]["BUY"] += 1
                        continue
                        
                    rel_drift = abs(delta_w) / target_w if target_w > 0 else 1.0
                    
                    # Apply policy
                    rebalance_this = False
                    
                    if s == "Pure Rebalance (0% Drift Filter)":
                        rebalance_this = abs(delta_w) > 0.002
                    elif s == "Symmetric 20% Drift (Current Engine)":
                        # Both buys and trims use 20% drift
                        if rel_drift > 0.20:
                            rebalance_this = True
                    elif s == "Asymmetric Hold Drift (Proposed)":
                        if delta_w < 0 and sig == SignalType.EQUAL_WEIGHT:
                            # Holding is over target, but analyst says HOLD
                            # Check 15% hard position cap
                            if cur_pos_w > 0.15:
                                new_w[t] = 0.15
                                turnover[s] += (cur_pos_w - 0.15)
                                num_trades[s] += 1
                                trades_by_action[s]["SELL"] += 1
                            elif rel_drift > hold_drift_tolerance:
                                # Breached the wider hold tolerance (e.g. >100% drift)
                                rebalance_this = True
                            else:
                                # PROTECTED HOLD: Let winner run!
                                rebalance_this = False
                        else:
                            # Normal 20% drift for Overweight or Underweight
                            if rel_drift > 0.20:
                                rebalance_this = True
                                
                    if rebalance_this:
                        new_w[t] = target_w
                        turnover[s] += abs(delta_w)
                        num_trades[s] += 1
                        if delta_w > 0:
                            trades_by_action[s]["BUY"] += 1
                        else:
                            trades_by_action[s]["SELL"] += 1
                            
                # Re-normalize equity weight <= 0.85
                tot_eq = sum(new_w.values())
                if tot_eq > 0.85:
                    excess = tot_eq - 0.85
                    scale = 0.85 / tot_eq
                    new_w = {t: w * scale for t, w in new_w.items()}
                weights[s] = new_w
                cash_weight[s] = max(0.15, 1.0 - sum(weights[s].values()))
                
        # 2. Daily return execution & price drift
        for s in strategies:
            cur_w = weights[s]
            eq_ret = sum(cur_w.get(t, 0.0) * day_rets.get(t, 0.0) for t in cur_w)
            c_ret = cash_weight[s] * cash_rate_daily
            total_day_ret = eq_ret + c_ret
            strat_returns[s].append(total_day_ret)
            
            # Drift weights to next day
            drifted_w = {}
            for t, w in cur_w.items():
                r_t = day_rets.get(t, 0.0)
                drifted_w[t] = w * (1.0 + r_t) / (1.0 + total_day_ret)
            weights[s] = drifted_w
            cash_weight[s] = cash_weight[s] * (1.0 + cash_rate_daily) / (1.0 + total_day_ret)
            
        strat_returns["S&P 500 (SPY)"].append(day_rets.get("SPY", 0.0))
        date_index.append(current_date)
        
    df_res = pd.DataFrame(strat_returns, index=date_index)
    years = len(df_res) / 252.0
    summary = {}
    
    for col in df_res.columns:
        r = df_res[col]
        tot_ret = (1.0 + r).prod() - 1.0
        cagr = (1.0 + tot_ret) ** (1.0 / years) - 1.0
        vol = r.std() * np.sqrt(252)
        rf = 0.03
        sharpe = (r.mean() * 252 - rf) / vol if vol > 0 else 0
        downside = r[r < 0].std() * np.sqrt(252)
        sortino = (r.mean() * 252 - rf) / downside if downside > 0 else 0
        cum = (1.0 + r).cumprod()
        dd = (cum - cum.cummax()) / cum.cummax()
        max_dd = dd.min()
        calmar = cagr / abs(max_dd) if abs(max_dd) > 0 else 0
        
        ann_turn = (turnover.get(col, 0.0) / years) * 100.0 if col in turnover else 0.0
        trades = num_trades.get(col, 0)
        sells = trades_by_action[col]["SELL"] if col in trades_by_action else 0
        buys = trades_by_action[col]["BUY"] if col in trades_by_action else 0
        
        summary[col] = {
            "Total Return %": tot_ret * 100,
            "CAGR %": cagr * 100,
            "Ann. Vol %": vol * 100,
            "Sharpe": sharpe,
            "Sortino": sortino,
            "Max DD %": max_dd * 100,
            "Calmar": calmar,
            "Ann. Turnover %": ann_turn,
            "Total Trades": trades,
            "Buys": buys,
            "Sells (Trims)": sells,
        }
        
    return pd.DataFrame(summary).T

print("\n" + "=" * 130)
print("🏆 PERIOD 1: FULL 4.75-YEAR CYCLE (Jan 2022 – Sept 2026)")
print("=" * 130)
res1 = run_simulation("2022-01-15", hold_drift_tolerance=1.00)
print(res1.round(2).to_string())

print("\n" + "=" * 130)
print("🏆 PERIOD 2: 4-YEAR EXPANSION CYCLE (July 2022 – Sept 2026)")
print("=" * 130)
res2 = run_simulation("2022-07-01", hold_drift_tolerance=1.00)
print(res2.round(2).to_string())
