import argparse
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True))
    sibling_env = BASE_DIR.parent / "TradingAgents" / ".env"
    if sibling_env.exists():
        load_dotenv(sibling_env, override=False)
except ImportError:
    pass

from pm.agents.bridge import TradingAgentsBridge
from pm.agents.triage import SignalTriageEngine
from pm.engine.controller import PortfolioManagerEngine
from pm.output.reporter import MarkdownTradeReporter
from pm.watcher import DownloadWatcher


def format_table(headers, rows):
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    header_line = " | ".join(f"{h:<{col_widths[i]}}" for i, h in enumerate(headers))
    sep_line = "-+-".join("-" * col_widths[i] for i in range(len(headers)))
    row_lines = [
        " | ".join(f"{str(cell):<{col_widths[i]}}" for i, cell in enumerate(row))
        for row in rows
    ]
    return "\n".join([header_line, sep_line] + row_lines)


def main():
    parser = argparse.ArgumentParser(description="Deterministic Portfolio Manager (PM) Engine")
    parser.add_argument("--csv", type=str, help="Path to Fidelity Portfolio_Positions CSV", default=None)
    parser.add_argument("--config", type=str, help="Path to config.yaml", default=str(BASE_DIR / "config.yaml"))
    parser.add_argument("--execute", action="store_true", help="Generate Trade_Orders_YYYY-MM-DD.md in Obsidian vault")
    parser.add_argument("--watch", action="store_true", help="Monitor ~/Downloads for new CSV and auto-execute")
    parser.add_argument("--preview", action="store_true", help="Display verification tables without writing", default=True)
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing daily report instead of appending a new snapshot")
    parser.add_argument("--validate-config", action="store_true", help="Validate configuration and exit")
    parser.add_argument("--backtest", type=str, help="Evaluate parameter grid against a price CSV")
    parser.add_argument("--backtest-output", type=str, default=None, help="Optional CSV path for backtest results")

    # New TradingAgents integration flags
    parser.add_argument("--triage", action="store_true", help="Audit watchlist and holdings reports to display evaluation queue")
    parser.add_argument("--run-agents", action="store_true", help="Run TradingAgents evaluation on queued or specified tickers")
    parser.add_argument("--pipeline", action="store_true", help="Run end-to-end: Triage -> Agents -> Solver -> Trade Orders")
    parser.add_argument("--tickers", type=str, default=None, help="Comma-separated tickers to evaluate (e.g. AAPL,NVDA)")
    parser.add_argument("--limit", type=int, default=None, help="Max number of tickers to evaluate with agents")
    parser.add_argument("--older-than", "--days", type=int, default=None, help="Re-evaluate reports older than N days (e.g. --older-than 3). Missing always included.")
    parser.add_argument("--all", "--refresh-all", "--force", action="store_true", help="Re-evaluate ALL reports regardless of age (shortcut for --older-than 0)")
    parser.add_argument("--category", type=str, default=None, help="Filter queue to a specific category (e.g. stocks_watchlist, etfs_index, fixed_income)")
    parser.add_argument("--queue", "--stale", "--queue-only", "--stale-only", action="store_true", help="Display only the queue of reports in need of refresh (omit fresh reports)")
    parser.add_argument("--trade-date", type=str, default=None, help="Target evaluation trade date (YYYY-MM-DD); defaults to latest settled trading day")

    args = parser.parse_args()

    if args.validate_config:
        from pm.config import validate_config
        import yaml
        with open(args.config, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        warnings = validate_config(config, args.config)
        print(f"Configuration valid: {args.config}")
        for warning in warnings:
            print(f"WARNING: {warning}")
        return

    if args.backtest:
        from pm.backtest import BacktestConfig, load_prices, run_parameter_sweep, write_results
        prices = load_prices(args.backtest)
        results = run_parameter_sweep(prices, BacktestConfig())
        print(results.to_string(index=False))
        if args.backtest_output:
            print(f"\nSaved backtest results to {write_results(results, args.backtest_output)}")
        return

    if args.watch:
        watcher = DownloadWatcher(config_path=args.config)
        watcher.run()
        return

    extra_tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else None
    )

    effective_min_age = 0 if args.all else args.older_than

    if args.queue:
        args.triage = True

    # 1. Triage Mode
    if args.triage:
        print("\n=======================================================")
        print("   🔍 PORTFOLIO MANAGER — SIGNAL AUDIT & TRIAGE       ")
        print("=======================================================\n")
        triage_engine = SignalTriageEngine(config_or_path=args.config)
        plan = triage_engine.run_triage(
            csv_path=args.csv,
            extra_tickers=extra_tickers,
            min_age_days=effective_min_age,
            category=args.category,
        )
        print(SignalTriageEngine.format_triage_table(plan, show_fresh=not args.queue))
        print()
        return

    # 2. Run Agents or Pipeline Stage
    if args.run_agents or args.pipeline:
        triage_engine = SignalTriageEngine(config_or_path=args.config)
        plan = triage_engine.run_triage(
            csv_path=args.csv,
            extra_tickers=extra_tickers,
            min_age_days=effective_min_age,
            category=args.category,
        )

        if extra_tickers:
            # If explicit tickers requested, evaluate them regardless of priority
            queue_to_run = [it for it in plan.items if it.ticker in extra_tickers]
        else:
            queue_to_run = plan.queue

        batch_limit = args.limit
        if batch_limit is None:
            if args.all or args.older_than is not None:
                # If user requested --all or an age threshold, run all matching unless capped
                batch_limit = len(queue_to_run)
            else:
                batch_limit = triage_engine.watchlist_cfg.get("max_batch_size", 5)

        if queue_to_run:
            bridge = TradingAgentsBridge(config_or_path=args.config)
            bridge.run_batch(queue_to_run, limit=batch_limit, trade_date=args.trade_date)
        else:
            print("✅ All tracked securities have fresh research reports on file. No agent evaluation needed.")

        if not args.pipeline:
            return

    print("\n=======================================================")
    print("   🚀 PORTFOLIO MANAGER (PM) ENGINE — SOLVER START    ")
    print("=======================================================\n")

    engine = PortfolioManagerEngine(config_path=args.config)
    summary = engine.run_solver(csv_path=args.csv)

    print(f"\n📈 Portfolio Live Value : ${summary.total_portfolio_value:,.2f}")
    print(f"💵 Available Cash       : ${summary.current_cash:,.2f} ({summary.current_cash/summary.total_portfolio_value*100:.1f}%)")
    print(f"🛡️ Target Cash Reserve  : ${summary.target_cash_reserve:,.2f} ({summary.target_cash_reserve/summary.total_portfolio_value*100:.1f}%)")
    print(f"🏦 Projected Ending Cash: ${summary.projected_ending_cash:,.2f} ({summary.projected_ending_cash/summary.total_portfolio_value*100:.1f}%)\n")
    if summary.safe_mode:
        print("⚠️  DATA QUALITY: SAFE MODE ACTIVE — new buy directives may be suppressed.")
    for warning in summary.data_quality_warnings:
        print(f"   ⚠️ {warning}")
    if summary.data_quality_warnings:
        print()

    # 1. Active Directives Table (sell orders then buy orders, alphabetically by ticker)
    active_orders = [a for a in summary.allocations if a.action in ("BUY", "SELL") and a.order_shares > 0]
    sells = sorted([a for a in active_orders if a.action == "SELL"], key=lambda a: a.ticker)
    buys = sorted([a for a in active_orders if a.action == "BUY"], key=lambda a: a.ticker)
    ordered_active = sells + buys

    print("=======================================================")
    print(f"🎯 ACTIONABLE TRADE DIRECTIVES ({len(ordered_active)} Orders)")
    print("=======================================================")
    if ordered_active:
        headers = ["Ticker", "Shares Held", "Price", "Target %", "Target $", "Delta ($)", "Action", "Order Shares", "Reason"]
        rows = []
        for a in ordered_active:
            rows.append([
                a.ticker,
                f"{a.current_shares:g}",
                f"${a.realtime_price:,.2f}",
                f"{a.target_weight:.2f}%",
                f"${a.target_value:,.2f}",
                f"${a.dollar_delta:+,.2f}",
                a.action,
                f"{a.order_shares:g}",
                a.reason,
            ])
        print(format_table(headers, rows))
    else:
        print("All assets aligned within 20% relative drift and $1,500 trade floor. No orders today.")

    # 2. Aging & Expiring Reports Table
    all_aging = summary.aging_signals + summary.expired_signals
    if all_aging:
        print("\n=======================================================")
        print(f"⏳ AGING & EXPIRING REPORTS ({len(all_aging)} Need Attention)")
        print("=======================================================")
        a_headers = ["Ticker", "Report Date", "Age (Days)", "Days Left", "Rating", "In Portfolio?", "Status"]
        a_rows = []
        for s in all_aging:
            status = "EXPIRED (>14d)" if s.is_expired else f"STALE SOON ({s.days_remaining}d left)"
            in_port = "YES" if s.in_portfolio else "No"
            a_rows.append([s.ticker, s.date.isoformat(), f"{s.age_days}d", f"{s.days_remaining}d", s.signal.value, in_port, status])
        print(format_table(a_headers, a_rows))

    # 3. Non-Portfolio Securities with Agent Reports
    non_port_signals = [s for s in summary.all_signals if not s.in_portfolio]
    if non_port_signals:
        print("\n=======================================================")
        print(f"🌐 NON-PORTFOLIO SECURITIES WITH AGENT REPORTS ({len(non_port_signals)} Tickers)")
        print("=======================================================")
        np_headers = ["Ticker", "Rating", "Target", "Report Date", "Age", "Days Left"]
        np_rows = []
        for s in sorted(non_port_signals, key=lambda x: x.ticker):
            tp_str = f"${s.target_price:,.2f}" if s.target_price is not None else "—"
            np_rows.append([s.ticker, s.signal.value, tp_str, s.date.isoformat(), f"{s.age_days}d", f"{s.days_remaining}d"])
        print(format_table(np_headers, np_rows))

    # 4. Securities without Active Agent Reports
    if summary.unreported_securities:
        print("\n=======================================================")
        print(f"⚠️ SECURITIES WITHOUT ACTIVE AGENT REPORTS ({len(summary.unreported_securities)} Tickers)")
        print("=======================================================")
        un_headers = ["Ticker", "In Portfolio?", "Shares Held", "Price", "Report Status", "Last Report Date", "Recommended Action"]
        un_rows = []
        for u in summary.unreported_securities:
            in_port = "YES" if u.in_portfolio else "No"
            shs_str = f"{u.shares_held:g}" if u.in_portfolio else "—"
            pr_str = f"${u.last_price:,.2f}" if u.last_price > 0 else "—"
            dt_str = u.last_report_date.isoformat() if u.last_report_date else "—"
            action_str = f"Run: python main.py --run-agents --tickers {u.ticker}"
            un_rows.append([u.ticker, in_port, shs_str, pr_str, u.status, dt_str, action_str])
        print(format_table(un_headers, un_rows))

    # 5. Correlated Clusters Summary
    print("\n=======================================================")
    print("🔗 CORRELATED ASSET CLUSTERS (Max 25% Exposure Cap)")
    print("=======================================================")
    alloc_map = {a.ticker: a for a in summary.allocations}
    c_headers = ["Cluster ID", "Combined Target %", "Cap Limit", "Assets"]
    c_rows = []
    for c_id, members in sorted(summary.clusters.items()):
        c_weight = sum(alloc_map[m].target_weight for m in members if m in alloc_map)
        c_rows.append([
            f"Cluster {c_id}",
            f"{c_weight:.2f}%",
            "25.00%",
            ", ".join(sorted(members)),
        ])
    print(format_table(c_headers, c_rows))

    # 4. Output Generation if --execute
    paths_cfg = engine.config.get("paths", {})
    output_cfg = engine.config.get("output", {})
    reporter = MarkdownTradeReporter(
        engine.obsidian_vault_dir,
        link_style=output_cfg.get("report_link_style", "markdown"),
        prefer_complete_report=output_cfg.get("prefer_complete_report", True),
        date_layout=output_cfg.get("date_layout", "stacked"),
        append_daily_snapshots=output_cfg.get("append_daily_snapshots", True),
        daily_pointer_file=paths_cfg.get("daily_pointer_file"),
    )
    if args.execute:
        saved_path = reporter.write_report(summary, overwrite=args.overwrite)
        print(f"\n✅ Trade orders markdown successfully written to:")
        print(f"   {saved_path}\n")
    else:
        print("\n💡 Running in PREVIEW mode. To write Trade_Orders_YYYY-MM-DD.md into your Obsidian vault, run with --execute.")


if __name__ == "__main__":
    main()
