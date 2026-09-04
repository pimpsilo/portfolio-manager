import sys
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

BASE_DIR = Path('/Users/matthewhope/github_projects/portfolio-manager')
sys.path.insert(0, str(BASE_DIR))

from pm.engine.controller import PortfolioManagerEngine
from pm.models import SignalType

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

    # 1. Realized Volatility
    vols = returns_df.std() * np.sqrt(252)
    med_vol = float(vols.median()) if len(vols) > 0 else 0.30
    vols_dict = {t: (float(vols[t]) if t in vols and not np.isnan(vols[t]) else med_vol) for t in sorted_universe}

    # 2. Expected Upside
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

    # 3. Market Caps (Fast Info via yfinance)
    print("Fetching market caps from yfinance...")
    tickers_obj = yf.Tickers(" ".join(sorted_universe))
    market_caps = {}
    for t in sorted_universe:
        try:
            # fast_info.market_cap
            mc = tickers_obj.tickers[t].fast_info.market_cap
            if mc is None or mc <= 0:
                mc = 50e9  # default $50B if ETF like TQQQ or unlisted
        except Exception:
            mc = 50e9
        market_caps[t] = mc

    # -------------------------------------------------------------
    # BASELINE: Equal Multipliers
    # -------------------------------------------------------------
    weights_base = engine.optimizer.optimize_weights(sorted_universe, active_signals, clusters)

    # -------------------------------------------------------------
    # OPTION 1: Upside-Weighted Sizing
    # -------------------------------------------------------------
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

    # -------------------------------------------------------------
    # OPTION 2: Inverse Volatility (Risk-Parity)
    # -------------------------------------------------------------
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

    # -------------------------------------------------------------
    # OPTION 5: Market-Cap / Maturity Anchor with Active Tilts
    # -------------------------------------------------------------
    # Tiered baseline:
    # Mega-Cap ($200B+): 3.0x base weight multiplier
    # Large-Cap ($50B - $200B): 1.8x base weight multiplier
    # Mid/Emerging (<$50B): 1.0x base weight multiplier
    # Multiplied by Signal: Overweight (1.5x), Equal (1.0x), Underweight (0.5x)
    opt5_mults = {}
    for t in sorted_universe:
        sig = active_signals[t]
        sig_m = 1.5 if sig == SignalType.OVERWEIGHT else (1.0 if sig == SignalType.EQUAL_WEIGHT else (0.5 if sig == SignalType.UNDERWEIGHT else 0.0))
        mc = market_caps[t]
        if mc >= 200e9:
            cap_m = 3.0   # Mega-cap anchor (e.g. MSFT, AAPL, NVDA, AMZN, GOOG, META, AMD, NFLX, COST)
        elif mc >= 50e9:
            cap_m = 1.8   # Large-cap core (e.g. SPGI, MCO, INTU, BKNG, UBER, MELI, CRM)
        else:
            cap_m = 1.0   # Mid/Emerging satellite (e.g. HIG, EG, CPRT, DECK, WDC, ALAB, RDDT)

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

    # -------------------------------------------------------------
    # PRINT 4-WAY COMPARISON TABLE
    # -------------------------------------------------------------
    print("\n" + "=" * 155)
    print(f"📊 4-WAY CONCENTRATION SIMULATION: BASELINE vs OPT 1 vs OPT 2 vs OPT 5 (Live Value: ${total_val:,.2f})")
    print("=" * 155)
    header = f"{'Ticker':<6} | {'Current $':<10} | {'Current %':<9} | {'Mkt Cap':<8} | {'Baseline $ (Wgt)':<18} | {'Opt 1: Upside $ (Wgt)':<22} | {'Opt 2: Risk-Parity $ (Wgt)':<26} | {'Opt 5: Market-Cap Anchor $ (Wgt)'}"
    print(header)
    print("-" * 155)

    sorted_holdings = sorted(sorted_universe, key=lambda x: (portfolio_state.holdings[x].current_value if x in portfolio_state.holdings else 0.0), reverse=True)

    for t in sorted_holdings[:24]:
        c_val = portfolio_state.holdings[t].current_value if t in portfolio_state.holdings else 0.0
        c_pct = (c_val / total_val) * 100
        mc_b = market_caps[t] / 1e9
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

    print("-" * 155)

if __name__ == "__main__":
    main()
