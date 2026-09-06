from datetime import date
from pathlib import Path
from typing import List, Optional
from pm.models import AllocationResult, ReconciliationSummary


class MarkdownTradeReporter:
    """
    Generates the structured Obsidian Trade Orders report: Trade_Orders_YYYY-MM-DD.md.
    """

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)

    def generate_report_markdown(self, summary: ReconciliationSummary) -> str:
        lines: List[str] = []

        exec_date = summary.execution_date.isoformat()
        lines.append(f"# Trade Execution Orders — {exec_date}")
        lines.append("")
        live_val = f"${summary.total_portfolio_value:,.2f}"
        curr_cash = f"${summary.current_cash:,.2f}"
        curr_cash_pct = f"{summary.current_cash/summary.total_portfolio_value*100:.1f}%"
        end_cash = f"${summary.projected_ending_cash:,.2f}"
        end_cash_pct = f"{summary.projected_ending_cash/summary.total_portfolio_value*100:.1f}%"
        lines.append(f"> **Portfolio Live Value**: **{live_val}** | **Current Cash**: **{curr_cash}** ({curr_cash_pct}) | **Projected Ending Cash**: **{end_cash}** ({end_cash_pct})")
        lines.append("")

        # 1. Active Directives Section
        active_orders = [a for a in summary.allocations if a.action in ("BUY", "SELL") and a.order_shares > 0]
        sells = [a for a in active_orders if a.action == "SELL"]
        buys = [a for a in active_orders if a.action == "BUY"]

        lines.append("## 🎯 Execution Directives (Actionable Trades)")
        lines.append("")
        if not active_orders:
            lines.append("*All positions are aligned within risk and drift bands. Zero trades required today.*")
            lines.append("")
        else:
            s_dollars = f"${summary.total_sells_dollars:,.2f}"
            b_dollars = f"${summary.total_buys_dollars:,.2f}"
            net_change = f"${(summary.total_sells_dollars - summary.total_buys_dollars):+,.2f}"
            lines.append(f"**Total Capital to Reallocate**: Sells: **{s_dollars}** | Buys: **{b_dollars}** | Net Cash Change: **{net_change}**")
            lines.append("")
            lines.append("| Ticker | Current Shares | Current Price (yfinance) | Target Weight | Target Value | Delta ($) | Action | Order Shares | Directive Rationale |")
            lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

            for a in sells + buys:
                curr_shares_str = f"{a.current_shares:g}"
                order_shares_str = f"{a.order_shares:g}"
                action_badge = "**SELL** 🔴" if a.action == "SELL" else "**BUY** 🟢"
                lines.append(
                    f"| **[[{a.ticker}]]** | {curr_shares_str} | ${a.realtime_price:,.2f} | {a.target_weight:.2f}% | ${a.target_value:,.2f} | ${a.dollar_delta:+,.2f} | {action_badge} | **{order_shares_str}** | {a.reason} |"
                )
            lines.append("")

        # 2. Aging & Approaching Stale Reports Section (Placed right after Directives and before Ledger)
        lines.append("## ⏳ Aging & Approaching Stale Reports (Needs Re-evaluation)")
        lines.append("")
        lines.append(f"> Reports older than **{summary.max_age_days} days** are considered stale. The items below require a fresh `tradingagents` analysis run before their signals expire.")
        lines.append("")

        all_aging = summary.aging_signals + summary.expired_signals
        if not all_aging:
            lines.append(f"*All active reports are fresh (less than {summary.max_age_days - 4} days old). Zero reports approaching expiration.*")
            lines.append("")
        else:
            lines.append("| Ticker | Report Date | Report Age | Days Left | Current Rating | In Portfolio? | Urgency & Action |")
            lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :--- |")

            for s in all_aging:
                in_port_str = "**YES** ✅" if s.in_portfolio else "No (Watchlist)"
                if s.is_expired:
                    status_badge = "🛑 **EXPIRED** (>14d) — Re-run urgently!" if s.in_portfolio else "🛑 **EXPIRED** — Candidate inactive"
                elif s.days_remaining <= 2:
                    status_badge = f"⚠️ **CRITICAL ({s.days_remaining}d left)** — Queue today"
                else:
                    status_badge = f"🟡 **WARNING ({s.days_remaining}d left)** — Queue this week"

                lines.append(
                    f"| **[[{s.ticker}]]** | {s.date.isoformat()} | {s.age_days} days | {s.days_remaining} days | {s.signal.value} | {in_port_str} | {status_badge} |"
                )
            lines.append("")

        # 3. Full Allocation Table
        lines.append("## 📊 Full Portfolio Rebalance & Drift Ledger")
        lines.append("")
        lines.append("| Ticker | Current Shares | Current Price (yfinance) | Target Weight | Target Value | Delta ($) | Action | Order Shares | Drift / Protection Rationale |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

        for a in summary.allocations:
            curr_shares_str = f"{a.current_shares:g}"
            order_shares_str = f"{a.order_shares:g}" if a.action != "HOLD" else "—"
            action_display = a.action
            if a.action == "SELL":
                action_display = "**SELL** 🔴"
            elif a.action == "BUY":
                action_display = "**BUY** 🟢"
            else:
                action_display = "HOLD 🟡"

            lines.append(
                f"| **[[{a.ticker}]]** | {curr_shares_str} | ${a.realtime_price:,.2f} | {a.target_weight:.2f}% | ${a.target_value:,.2f} | ${a.dollar_delta:+,.2f} | {action_display} | {order_shares_str} | {a.reason} |"
            )


        lines.append("")

        # 4. Cluster Exposures
        lines.append("## 🔗 Correlated Asset Clusters & Exposure")
        lines.append("")
        lines.append("| Cluster ID | Group Assets | Combined Target Weight | Cap Limit | Status |")
        lines.append("| :---: | :--- | :---: | :---: | :---: |")

        alloc_by_ticker = {a.ticker: a for a in summary.allocations}
        for c_id, members in sorted(summary.clusters.items()):
            c_weight = sum(alloc_by_ticker[m].target_weight for m in members if m in alloc_by_ticker)
            assets_str = ", ".join(f"[[{m}]]" for m in sorted(members))
            status = "✅ OK" if c_weight <= 25.01 else "⚠️ CAPPED"
            lines.append(f"| {c_id} | {assets_str} | {c_weight:.2f}% | 25.00% | {status} |")

        lines.append("")
        return "\n".join(lines)

    def write_report(self, summary: ReconciliationSummary, filename: Optional[str] = None) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not filename:
            filename = f"Trade_Orders_{summary.execution_date.isoformat()}.md"

        target_path = self.output_dir / filename
        content = self.generate_report_markdown(summary)
        target_path.write_text(content, encoding="utf-8")
        return target_path
