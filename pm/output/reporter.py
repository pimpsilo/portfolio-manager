import os
import re
from datetime import date
from pathlib import Path
from typing import List, Optional, Union
from urllib.parse import quote
from pm.models import AllocationResult, ReconciliationSummary

_RUN_DIR_RE = re.compile(r"^[A-Z0-9._-]+_\d{8}_\d{6}$")
_RUN_DIR_DATE_RE = re.compile(r"_(\d{4})(\d{2})(\d{2})_\d{6}")


class MarkdownTradeReporter:
    """
    Generates the structured Obsidian Trade Orders report: Trade_Orders_YYYY-MM-DD.md.
    """

    def __init__(
        self,
        output_dir: str,
        link_style: str = "markdown",
        prefer_complete_report: bool = True,
        date_layout: str = "stacked",
    ):
        self.output_dir = Path(output_dir)
        self.link_style = link_style
        self.prefer_complete_report = prefer_complete_report
        self.date_layout = date_layout

    def _report_md_link(
        self,
        ticker: str,
        source_path: Optional[str] = None,
        report_date: Optional[Union[date, str]] = None,
        bold: bool = True,
        include_date: bool = True,
    ) -> str:
        """
        Return a table-safe Markdown link to the source agent report, optionally
        accompanied by an ~8pt report date (YYYY-MM-DD), or the bare ticker
        when no report is available.

        Example (stacked):
        **[AAPL](../01_agent_reports/AAPL/AAPL_20260905_105303/complete_report.md)**<br><span style="font-size: 8pt; opacity: 0.7;">2026-09-05</span>
        """
        if not source_path:
            return f"**{ticker}**" if bold else ticker

        target = Path(source_path)

        # Walk up to the run folder (<TICKER>_<YYYYMMDD>_<HHMMSS>)
        run_dir = next(
            (p for p in (target.parent, *target.parents) if _RUN_DIR_RE.match(p.name)),
            None,
        )
        if self.prefer_complete_report and run_dir is not None and (run_dir / "complete_report.md").exists():
            target = run_dir / "complete_report.md"

        # Resolve date string if requested
        date_str = None
        if include_date:
            if report_date is not None:
                date_str = report_date.isoformat() if hasattr(report_date, "isoformat") else str(report_date)
            elif run_dir is not None:
                match = _RUN_DIR_DATE_RE.search(run_dir.name)
                if match:
                    date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

        # Compute relative path to vault output directory
        try:
            rel = os.path.relpath(target, start=str(self.output_dir)).replace(os.sep, "/")
        except ValueError:
            rel = str(target).replace(os.sep, "/")

        # Format link base
        if self.link_style == "wikilink":
            link = f"[[{rel}\\|{ticker}]]"
        elif self.link_style == "none":
            link = ticker
        else:
            link = f"[{ticker}]({quote(rel)})"

        linked_ticker = f"**{link}**" if bold else link

        if date_str and self.link_style != "none":
            date_badge = f'<span style="font-size: 8pt; opacity: 0.7;">{date_str}</span>'
            if self.date_layout == "stacked":
                return f"{linked_ticker}<br>{date_badge}"
            else:
                return f"{linked_ticker} {date_badge}"

        return linked_ticker

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
                ticker_cell = self._report_md_link(a.ticker, a.report_path, a.report_date, bold=True, include_date=True)
                lines.append(
                    f"| {ticker_cell} | {curr_shares_str} | ${a.realtime_price:,.2f} | {a.target_weight:.2f}% | ${a.target_value:,.2f} | ${a.dollar_delta:+,.2f} | {action_badge} | **{order_shares_str}** | {a.reason} |"
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

                ticker_cell = self._report_md_link(s.ticker, s.source_path, s.date, bold=True, include_date=False)
                lines.append(
                    f"| {ticker_cell} | {s.date.isoformat()} | {s.age_days} days | {s.days_remaining} days | {s.signal.value} | {in_port_str} | {status_badge} |"
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

            ticker_cell = self._report_md_link(a.ticker, a.report_path, a.report_date, bold=True, include_date=True)
            lines.append(
                f"| {ticker_cell} | {curr_shares_str} | ${a.realtime_price:,.2f} | {a.target_weight:.2f}% | ${a.target_value:,.2f} | ${a.dollar_delta:+,.2f} | {action_display} | {order_shares_str} | {a.reason} |"
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
            assets_str = ", ".join(
                self._report_md_link(
                    m,
                    alloc_by_ticker[m].report_path if m in alloc_by_ticker else None,
                    alloc_by_ticker[m].report_date if m in alloc_by_ticker else None,
                    bold=False,
                    include_date=False,
                )
                for m in sorted(members)
            )
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

