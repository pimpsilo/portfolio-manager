import sys
from pathlib import Path
import numpy as np
import pandas as pd

BASE_DIR = Path('/Users/matthewhope/github_projects/portfolio-manager')
sys.path.insert(0, str(BASE_DIR))

from pm.engine.controller import PortfolioManagerEngine
from pm.models import SignalType

# Institutional Market Cap classifications
MARKET_CAPS = {
    "AAPL": 3450e9, "MSFT": 3300e9, "NVDA": 3100e9, "AMZN": 2100e9,
    "GOOG": 2050e9, "GOOGL": 2050e9, "META": 1550e9, "MA": 480e9,
    "COST": 410e9, "ASML": 360e9, "NFLX": 310e9, "AMD": 245e9,
    "CRM": 235e9, "ADBE": 220e9, "INTU": 185e9, "BKNG": 165e9,
    "SPGI": 155e9, "UBER": 150e9, "PDD": 130e9, "MELI": 95e9,
    "MCO": 92e9, "CPRT": 52e9, "TQQQ": 50e9, "APP": 48e9,
    "HIG": 42e9, "KEYS": 28e9, "DECK": 25e9, "WDC": 24e9,
    "LULU": 22e9, "EG": 18e9, "ALAB": 16e9, "RDDT": 15e9,
    "DUOL": 12e9, "IMAX": 2e9,
}

def main():
    engine = PortfolioManagerEngine(config_path=str(BASE_DIR / 'config.yaml'))
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

    returns_df = engine.market_data.fetch_historical_returns(sorted_universe, period='6mo')
    clusters = engine.cluster_engine.cluster_assets(returns_df)

    vols = returns_df.std() * np.sqrt(252)
    med_vol = float(vols.median()) if len(vols) > 0 else 0.30
    vols_dict = {t: (float(vols[t]) if t in vols and not np.isnan(vols[t]) else med_vol) for t in sorted_universe}

    upsides = {}
    for t in sorted_universe:
        p_live = realtime_prices.get(t, 100.0)
        target = parsed_signals[t].target_price if (t in parsed_signals and parsed_signals[t].target_price) else None
        if target and target > 0:
            upside = max(0.0, (target - p_live) / p_live)
        else:
            sig = active_signals[t]
            upside = 0.20 if sig == SignalType.OVERWEIGHT else (0.10 if sig == SignalType.EQUAL_WEIGHT else 0.03)
        upsides[t] = upside

    # 1. Baseline: Equal Multipliers (Overweight=1.5, Equal=1.0)
    weights_base = engine.optimizer.optimize_weights(sorted_universe, active_signals, clusters)

    # 2. Option 1: Upside-Weighted Sizing
    opt1_mults = {}
    for t in sorted_universe:
        sig = active_signals[t]
        base_m = 1.5 if sig == SignalType.OVERWEIGHT else (1.0 if sig == SignalType.EQUAL_WEIGHT else (0.5 if sig == SignalType.UNDERWEIGHT else 0.0))
        up_mult = 1.0 + (upsides[t] * 3.0)
        opt1_mults[t] = base_m * up_mult if sig != SignalType.AVOID else 0.0

    tot_1 = sum(opt1_mults.values())
    weights_opt1 = {t: (v / tot_1) * 0.90 for t, v in opt1_mults.items()}
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

    # 3. Option 2: Inverse Volatility (Risk-Parity)
    opt2_mults = {}
    for t in sorted_universe:
        sig = active_signals[t]
        base_m = 1.5 if sig == SignalType.OVERWEIGHT else (1.0 if sig == SignalType.EQUAL_WEIGHT else (0.5 if sig == SignalType.UNDERWEIGHT else 0.0))
        v = max(0.12, vols_dict[t])
        opt2_mults[t] = base_m * (1.0 / v) if sig != SignalType.AVOID else 0.0

    tot_2 = sum(opt2_mults.values())
    weights_opt2 = {t: (v / tot_2) * 0.90 for t, v in opt2_mults.items()}
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

    # 4. Option 5: Market-Cap Anchor with Active Tilts
    # Mega-Caps ($200B+): 3.0x base anchor multiplier
    # Large-Caps ($50B - $200B): 1.8x base multiplier
    # Mid/Emerging (<$50B): 1.0x base multiplier
    opt5_mults = {}
    for t in sorted_universe:
        sig = active_signals[t]
        sig_m = 1.5 if sig == SignalType.OVERWEIGHT else (1.0 if sig == SignalType.EQUAL_WEIGHT else (0.5 if sig == SignalType.UNDERWEIGHT else 0.0))
        mc = MARKET_CAPS.get(t, 35e9)
        if mc >= 200e9:
            cap_m = 3.0
        elif mc >= 50e9:
            cap_m = 1.8
        else:
            cap_m = 1.0
        opt5_mults[t] = cap_m * sig_m if sig != SignalType.AVOID else 0.0

    tot_5 = sum(opt5_mults.values())
    weights_opt5 = {t: (v / tot_5) * 0.90 for t, v in opt5_mults.items()}
    for _ in range(5):
        for c_id, members in clusters.items():
            c_sum = sum(weights_opt5[m] for m in members)
            if c_sum > 0.25:
                scale = 0.25 / c_sum
                for m in members:
                    weights_opt5[m] *= scale
        for t in weights_opt5:
            if weights_opt5[t] > 0.15:
                weights_opt5[t] = 0.15

    print("\n" + "=" * 165)
    print(f"📊 4-WAY CONCENTRATION SIMULATION (Live Total Value: ${total_val:,.2f})")
    print("=" * 165)
    header = f"{'Ticker':<6} | {'Current $':<10} | {'Current %':<9} | {'Mkt Cap':<8} | {'Baseline $ (Wgt)':<18} | {'Opt 1: Upside $ (Wgt)':<22} | {'Opt 2: Risk-Parity $ (Wgt)':<26} | {'Opt 5: Market-Cap Anchor $ (Wgt)'}"
    print(header)
    print("-" * 165)

    sorted_holdings = sorted(sorted_universe, key=lambda x: (portfolio_state.holdings[x].current_value if x in portfolio_state.holdings else 0.0), reverse=True)

    for t in sorted_holdings[:24]:
        c_val = portfolio_state.holdings[t].current_value if t in portfolio_state.holdings else 0.0
        c_pct = (c_val / total_val) * 100
        mc_b = MARKET_CAPS.get(t, 35e9) / 1e9
        mc_str = f"${mc_b:,.0f}B"

        wb = weights_base[t] * 100
        db = wb * total_val / 100

        w1 = weights_opt1[t] * 100
        d1 = w1 * total_val / 100

        w2 = weights_opt2[t] * 100
        d2 = w2 * total_val / 100

        w5 = weights_opt5[t] * 100
        d5 = w5 * total_val / 100

        line = f"{t:<6} | ${c_val:>9,.0f} | {c_pct:>8.2f}% | {mc_str:>8} | ${db:>6,.0f} ({wb:>4.2f}%) | ${d1:>7,.0f} ({w1:>4.2f}%)   | ${d2:>7,.0f} ({w2:>4.2f}%)      | ${d5:>7,.0f} ({w5:>4.2f}%)"
        print(line)

    print("-" * 165)

if __name__ == "__main__":
    main()
