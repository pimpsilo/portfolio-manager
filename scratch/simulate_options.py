import sys
from pathlib import Path
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from pm.engine.controller import PortfolioManagerEngine
from pm.models import SignalType

def run_simulation():
    engine = PortfolioManagerEngine()
    portfolio_state = engine.csv_parser.parse()
    parsed_signals = engine.signal_parser.parse_all_signals()

    universe_tickers = set(portfolio_state.holdings.keys())
    for ticker, sig in parsed_signals.items():
        if not sig.is_expired and sig.signal == engine.candidate_min_signal:
            universe_tickers.add(ticker)
    sorted_universe = sorted(universe_tickers)

    active_signals = {}
    for t in sorted_universe:
        if t in parsed_signals and not parsed_signals[t].is_expired:
            active_signals[t] = parsed_signals[t].signal
        else:
            active_signals[t] = SignalType.EQUAL_WEIGHT

    fallback_prices = {s: h.last_price for s, h in portfolio_state.holdings.items()}
    realtime_prices = engine.market_data.fetch_realtime_prices(sorted_universe, fallback_prices=fallback_prices)
    
    current_equity_live = sum(
        portfolio_state.holdings[s].quantity * realtime_prices.get(s, portfolio_state.holdings[s].last_price)
        for s in portfolio_state.holdings
    )
    total_val = current_equity_live + portfolio_state.cash_balance

    # 6-Month Returns & Volatility
    returns_df = engine.market_data.fetch_historical_returns(sorted_universe, period="6mo")
    clusters = engine.cluster_engine.cluster_assets(returns_df)

    # Volatilities (annualized)
    vols = returns_df.std() * np.sqrt(252)
    # Fill any missing
    med_vol = vols.median() if len(vols) > 0 else 0.30
    vols = {t: (vols[t] if t in vols and not np.isnan(vols[t]) else med_vol) for t in sorted_universe}

    # Expected Upside
    upsides = {}
    for t in sorted_universe:
        p_live = realtime_prices.get(t, 100.0)
        target = parsed_signals[t].target_price if (t in parsed_signals and parsed_signals[t].target_price) else None
        if target and target > 0:
            upside = max(0.0, (target - p_live) / p_live)
        else:
            # Signal fallback upside
            sig = active_signals[t]
            upside = 0.20 if sig == SignalType.OVERWEIGHT else (0.10 if sig == SignalType.EQUAL_WEIGHT else 0.03)
        upsides[t] = upside

    # -------------------------------------------------------------
    # 1. BASELINE: Current Uniform Multipliers
    # -------------------------------------------------------------
    weights_baseline = engine.optimizer.optimize_weights(sorted_universe, active_signals, clusters)

    # -------------------------------------------------------------
    # 2. OPTION 1: Expected Upside / Payoff Weighted
    # -------------------------------------------------------------
    # Multiplier scales with: SignalMultiplier * (1 + Upside * 3)
    opt1_multipliers = {}
    for t in sorted_universe:
        sig = active_signals[t]
        base_m = 1.5 if sig == SignalType.OVERWEIGHT else (1.0 if sig == SignalType.EQUAL_WEIGHT else (0.5 if sig == SignalType.UNDERWEIGHT else 0.0))
        # Boost multiplier based on upside (e.g. 20% upside -> 1.5 * 1.6 = 2.4x; 5% upside -> 1.5 * 1.15 = 1.7x)
        up_mult = 1.0 + (upsides[t] * 3.0)
        opt1_multipliers[t] = base_m * up_mult if sig != SignalType.AVOID else 0.0

    # Custom optimizer run for Option 1
    raw_opt1 = {t: opt1_multipliers[t] for t in sorted_universe}
    tot_opt1 = sum(raw_opt1.values())
    weights_opt1 = {t: (v / tot_opt1) * 0.90 for t, v in raw_opt1.items()}
    # Apply single cap 15% and cluster cap 25%
    for _ in range(5):
        for c_id, members in clusters.items():
            c_sum = sum(weights_opt1[m] for m in members)
            if c_sum > 0.25:
                scale = 0.25 / c_sum
                for m in members:
                    weights_opt1[m] *= scale
        for t in weights_opt1:
            if weights_opt1[t] > 0.15:
                weights_opt1[t] = 0.15

    # -------------------------------------------------------------
    # 3. OPTION 2: Inverse Volatility / Risk Parity Weighted
    # -------------------------------------------------------------
    opt2_multipliers = {}
    for t in sorted_universe:
        sig = active_signals[t]
        base_m = 1.5 if sig == SignalType.OVERWEIGHT else (1.0 if sig == SignalType.EQUAL_WEIGHT else (0.5 if sig == SignalType.UNDERWEIGHT else 0.0))
        vol = max(0.12, vols[t])
        inv_vol = 1.0 / vol
        opt2_multipliers[t] = base_m * inv_vol if sig != SignalType.AVOID else 0.0

    tot_opt2 = sum(opt2_multipliers.values())
    weights_opt2 = {t: (v / tot_opt2) * 0.90 for t, v in opt2_multipliers.items()}
    for _ in range(5):
        for c_id, members in clusters.items():
            c_sum = sum(weights_opt2[m] for m in members)
            if c_sum > 0.25:
                scale = 0.25 / c_sum
                for m in members:
                    weights_opt2[m] *= scale
        for t in weights_opt2:
            if weights_opt2[t] > 0.15:
                weights_opt2[t] = 0.15

    # -------------------------------------------------------------
    # PRINT COMPARISON TABLE FOR TOP HOLDINGS & HIGH-UPSIDE ASSETS
    # -------------------------------------------------------------
    print("\n=========================================================================================================")
    print(f"📊 PORTFOLIO CONCENTRATION SIMULATION (Live Portfolio Value: ${total_val:,.2f})")
    print("=========================================================================================================")
    print(f"{'Ticker':<6} | {'Current $':<10} | {'Current %':<9} | {'Ann. Vol':<9} | {'Upside %':<9} | {'Baseline % ($)':<18} | {'Option 1: Upside':<18} | {'Option 2: Risk-Parity':<18}")
    print("-" * 105)

    # Sort by current holding value
    sorted_by_val = sorted(sorted_universe, key=lambda t: (portfolio_state.holdings[t].current_value if t in portfolio_state.holdings else 0.0), reverse=True)

    for t in sorted_by_val[:20]:
        curr_val = portfolio_state.holdings[t].current_value if t in portfolio_state.holdings else 0.0
        curr_pct = (curr_val / total_val) * 100
        vol_pct = vols[t] * 100
        up_pct = upsides[t] * 100

        w_base = weights_baseline.get(t, 0.0) * 100
        d_base = w_base * total_val / 100

        w_1 = weights_opt1.get(t, 0.0) * 100
        d_1 = w_1 * total_val / 100

        w_2 = weights_opt2.get(t, 0.0) * 100
        d_2 = w_2 * total_val / 100

        print(f"{t:<6} | ${curr_val:>9,.0f} | {curr_pct:>8.2f}% | {vol_pct:>8.1f}% | {up_pct:>8.1f}% | {w_base:>5.2f}% (${d_base:>6,.0f}) | {w_1:>5.2f}% (${d_1:>6,.0f}) | {w_2:>5.2f}% (${d_2:>6,.0f})")

    print("-" * 105)

if __name__ == "__main__":
    run_simulation()
