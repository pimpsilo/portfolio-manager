import argparse
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from pm.engine.controller import PortfolioManagerEngine
from pm.output.reporter import MarkdownTradeReporter


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
    parser.add_argument("--preview", action="store_true", help="Display verification tables without writing", default=True)

    args = parser.parse_args()

    print("\n=======================================================")
    print("   🚀 PORTFOLIO MANAGER (PM) ENGINE — SOLVER START    ")
    print("=======================================================\n")

    engine = PortfolioManagerEngine(config_path=args.config)
    summary = engine.run_solver(csv_path=args.csv)

    print(f"\n📈 Portfolio Live Value : ${summary.total_portfolio_value:,.2f}")
    print(f"💵 Available Cash       : ${summary.current_cash:,.2f} ({summary.current_cash/summary.total_portfolio_value*100:.1f}%)")
    print(f"🛡️ Target Cash Reserve  : ${summary.target_cash_reserve:,.2f} ({summary.target_cash_reserve/summary.total_portfolio_value*100:.1f}%)")
    print(f"🏦 Projected Ending Cash: ${summary.projected_ending_cash:,.2f} ({summary.projected_ending_cash/summary.total_portfolio_value*100:.1f}%)\n")

    # 1. Active Directives Table
    active_orders = [a for a in summary.allocations if a.action in ("BUY", "SELL") and a.order_shares > 0]
    print("=======================================================")
    print(f"🎯 ACTIONABLE TRADE DIRECTIVES ({len(active_orders)} Orders)")
    print("=======================================================")
    if active_orders:
        headers = ["Ticker", "Shares Held", "Price", "Target %", "Target $", "Delta ($)", "Action", "Order Shares", "Reason"]
        rows = []
        for a in active_orders:
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

    # 2. Correlated Clusters Summary
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

    # 3. Output Generation if --execute
    reporter = MarkdownTradeReporter(engine.obsidian_vault_dir)
    if args.execute:
        saved_path = reporter.write_report(summary)
        print(f"\n✅ Trade orders markdown successfully written to:")
        print(f"   {saved_path}\n")
    else:
        print("\n💡 Running in PREVIEW mode. To write Trade_Orders_YYYY-MM-DD.md into your Obsidian vault, run with --execute.")


if __name__ == "__main__":
    main()
