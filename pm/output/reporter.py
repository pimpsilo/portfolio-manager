import os
import re
from datetime import date
from pathlib import Path
from typing import List, Optional, Union
from urllib.parse import quote
from pm.models import AllocationResult, ParsedSignal, ReconciliationSummary, SignalType

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
        append_daily_snapshots: bool = True,
    ):
        self.output_dir = Path(output_dir)
        self.link_style = link_style
        self.prefer_complete_report = prefer_complete_report
        self.date_layout = date_layout
        self.append_daily_snapshots = append_daily_snapshots

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

    @staticmethod
    def _rating_badge(signal_val: SignalType) -> str:
        if signal_val == SignalType.OVERWEIGHT:
            return "OVERWEIGHT 🟢"
        elif signal_val == SignalType.EQUAL_WEIGHT:
            return "HOLD 🟡"
        elif signal_val == SignalType.UNDERWEIGHT:
            return "UNDERWEIGHT 🟠"
        elif signal_val == SignalType.AVOID:
            return "AVOID 🔴"
        return str(signal_val.value if hasattr(signal_val, "value") else signal_val)

    def _snapshot_header(self, summary: ReconciliationSummary) -> str:
        if summary.download_time and summary.source_file:
            return f"## ⏱️ Snapshot: {summary.download_time} (Source: `{summary.source_file}`)"
        elif summary.download_time:
            return f"## ⏱️ Snapshot: {summary.download_time}"
        elif summary.source_file:
            return f"## ⏱️ Snapshot: (Source: `{summary.source_file}`)"
        else:
            return "## ⏱️ Snapshot: Portfolio Reconciliation"

    def generate_snapshot_markdown(self, summary: ReconciliationSummary) -> str:
        lines: List[str] = []

        lines.append(self._snapshot_header(summary))
        lines.append("")
        live_val = f"${summary.total_portfolio_value:,.2f}"
        curr_cash = f"${summary.current_cash:,.2f}"
        curr_cash_pct = f"{summary.current_cash/summary.total_portfolio_value*100:.1f}%"
        end_cash = f"${summary.projected_ending_cash:,.2f}"
        end_cash_pct = f"{summary.projected_ending_cash/summary.total_portfolio_value*100:.1f}%"
        lines.append(f"> **Portfolio Live Value**: **{live_val}** | **Current Cash**: **{curr_cash}** ({curr_cash_pct}) | **Projected Ending Cash**: **{end_cash}** ({end_cash_pct})")

        meta_items = []
        if summary.source_file:
            meta_items.append(f"**Source Export**: `{summary.source_file}`")
        if summary.download_time:
            meta_items.append(f"**Downloaded**: {summary.download_time}")
        if summary.execution_timestamp:
            meta_items.append(f"**Solver Run**: {summary.execution_timestamp}")
        if meta_items:
            lines.append(f"> {' | '.join(meta_items)}")
        lines.append("")

        alloc_by_ticker = {a.ticker: a for a in summary.allocations}

        # 1. Immediate / Daily Actions (sell orders then buy orders, alphabetically by ticker)
        active_orders = [a for a in summary.allocations if a.action in ("BUY", "SELL") and a.order_shares > 0]
        sells = sorted([a for a in active_orders if a.action == "SELL"], key=lambda a: a.ticker)
        buys = sorted([a for a in active_orders if a.action == "BUY"], key=lambda a: a.ticker)

        lines.append("## 🎯 1. Immediate / Daily Actions")
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

        # 2. Aging / Stale Reports Summary
        lines.append("## ⏳ 2. Aging / Stale Reports Summary")
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

        # 3. Full Portfolio Rebalance & Drift Ledger
        lines.append("## 📊 3. Full Portfolio Rebalance & Drift Ledger")
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

        # 4. Non-Portfolio Securities with Active Agent Reports & Status
        lines.append("## 🌐 4. Non-Portfolio Securities with Active Agent Reports & Status")
        lines.append("")

        # Gather unheld signals
        non_port_signals: List[ParsedSignal] = []
        if summary.all_signals:
            non_port_signals = [s for s in summary.all_signals if not s.in_portfolio]
        else:
            non_port_signals = [s for s in summary.aging_signals + summary.expired_signals if not s.in_portfolio]

        non_port_signals.sort(key=lambda s: s.ticker)

        if not non_port_signals:
            lines.append("*No unheld securities with agent reports found.*")
            lines.append("")
        else:
            lines.append("| Ticker | Current Rating | Target Price | Report Date | Report Age | Days Left | Portfolio Status & Guidance |")
            lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :--- |")

            for s in non_port_signals:
                rating_badge = self._rating_badge(s.signal)
                tp_str = f"${s.target_price:,.2f}" if s.target_price is not None else "—"
                alloc = alloc_by_ticker.get(s.ticker)

                if s.is_expired:
                    status_guidance = "🛑 Expired (>14d) — Inactive candidate; queue fresh agent evaluation"
                elif s.signal == SignalType.OVERWEIGHT:
                    if alloc and alloc.action == "BUY":
                        status_guidance = f"🟢 Buy Directive — Target {alloc.target_weight:.2f}% (${alloc.target_value:,.2f}), Order {alloc.order_shares:g} shs"
                    elif alloc and alloc.action == "HOLD":
                        status_guidance = f"🟡 Candidate Held — {alloc.reason}"
                    else:
                        status_guidance = "🟢 Active Candidate — Overweight signal; awaiting entry allocation"
                elif s.signal == SignalType.EQUAL_WEIGHT:
                    if s.is_approaching_stale:
                        status_guidance = f"🟡 Watchlist (Hold) — Neutral; {s.days_remaining}d left before re-evaluation"
                    else:
                        status_guidance = "Watchlist (Hold) — Neutral; awaiting Overweight catalyst for entry"
                elif s.signal == SignalType.UNDERWEIGHT:
                    status_guidance = "Watchlist (Underweight) — Defensive stance; ineligible for capital entry"
                elif s.signal == SignalType.AVOID:
                    status_guidance = "Watchlist (Avoid) — Ineligible for capital entry"
                else:
                    status_guidance = "Watchlist — Monitoring"

                age_badge = f"{s.age_days} days"
                days_left_badge = f"🟡 **{s.days_remaining}d**" if s.is_approaching_stale else (f"{s.days_remaining} days" if not s.is_expired else "🛑 Expired")
                ticker_cell = self._report_md_link(s.ticker, s.source_path, s.date, bold=True, include_date=False)

                lines.append(
                    f"| {ticker_cell} | {rating_badge} | {tp_str} | {s.date.isoformat()} | {age_badge} | {days_left_badge} | {status_guidance} |"
                )
            lines.append("")

        # 5. All Securities with Agent Reports (Alphabetical by Ticker)
        lines.append("## 📋 5. All Securities with Agent Reports")
        lines.append("")
        lines.append("> Complete directory of all securities with agent research reports on file, including analyst verdicts, price targets, core guidance summaries, and current portfolio participation.")
        lines.append("")

        all_sigs = list(summary.all_signals) if summary.all_signals else []
        if not all_sigs:
            # Fallback if all_signals wasn't passed
            seen = set()
            for s in summary.aging_signals + summary.expired_signals:
                if s.ticker not in seen:
                    all_sigs.append(s)
                    seen.add(s.ticker)
            for a in summary.allocations:
                if a.ticker not in seen:
                    all_sigs.append(ParsedSignal(
                        ticker=a.ticker,
                        signal=a.signal,
                        date=a.report_date or summary.execution_date,
                        source_path=a.report_path or "",
                        raw_rating=a.signal.value,
                        in_portfolio=a.current_shares > 0,
                    ))
                    seen.add(a.ticker)

        all_sigs.sort(key=lambda s: s.ticker)

        if not all_sigs:
            lines.append("*No agent reports on file.*")
            lines.append("")
        else:
            lines.append("| Ticker | Verdict / Rating | Price Target | Portfolio Participation | Core Guidance Summary |")
            lines.append("| :--- | :---: | :---: | :--- | :--- |")

            for s in all_sigs:
                rating_badge = self._rating_badge(s.signal)
                tp_str = f"${s.target_price:,.2f}" if s.target_price is not None else "—"
                alloc = alloc_by_ticker.get(s.ticker)

                if s.in_portfolio and alloc and alloc.current_shares > 0:
                    participation = f"**Held** ({alloc.current_shares:g} shs · {alloc.current_weight:.2f}%)"
                elif s.in_portfolio:
                    participation = "**Held** (0 shs)"
                else:
                    participation = "No (0 shs · Watchlist)"

                guidance = s.core_guidance.strip() if s.core_guidance else "No executive summary provided."
                guidance_clean = " ".join(guidance.split()).replace("|", "-")

                ticker_cell = self._report_md_link(s.ticker, s.source_path, s.date, bold=True, include_date=False)
                lines.append(
                    f"| {ticker_cell} | {rating_badge} | {tp_str} | {participation} | {guidance_clean} |"
                )
            lines.append("")

        # 6. Cluster Exposures
        lines.append("## 🔗 Correlated Asset Clusters & Exposure")
        lines.append("")
        lines.append("| Cluster ID | Group Assets | Combined Target Weight | Cap Limit | Status |")
        lines.append("| :---: | :--- | :---: | :---: | :---: |")

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

    def generate_report_markdown(self, summary: ReconciliationSummary) -> str:
        exec_date = summary.execution_date.isoformat()
        return f"# Trade Execution Orders — {exec_date}\n\n" + self.generate_snapshot_markdown(summary)

    def write_report(
        self,
        summary: ReconciliationSummary,
        filename: Optional[str] = None,
        overwrite: bool = False,
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not filename:
            filename = f"Trade_Orders_{summary.execution_date.isoformat()}.md"

        target_path = self.output_dir / filename

        if overwrite or not self.append_daily_snapshots or not target_path.exists():
            content = self.generate_report_markdown(summary)
            target_path.write_text(content, encoding="utf-8")
            return target_path

        existing_content = target_path.read_text(encoding="utf-8")
        if not existing_content.strip() or "## ⏱️ Snapshot:" not in existing_content:
            content = self.generate_report_markdown(summary)
            target_path.write_text(content, encoding="utf-8")
            return target_path

        snapshot_body = self.generate_snapshot_markdown(summary)
        source_id = summary.source_file

        # If source_id is present and already in existing_content, replace that snapshot block idempotently
        if source_id and (f"`{source_id}`" in existing_content or source_id in existing_content):
            blocks = re.split(r"\n+---\n+", existing_content)
            new_blocks = []
            replaced = False
            for i, block in enumerate(blocks):
                if source_id in block:
                    if i == 0 and block.startswith("# "):
                        first_line = block.splitlines()[0]
                        new_blocks.append(f"{first_line}\n\n{snapshot_body}")
                    else:
                        new_blocks.append(snapshot_body)
                    replaced = True
                else:
                    new_blocks.append(block)
            if replaced:
                target_path.write_text("\n\n---\n\n".join(new_blocks).rstrip() + "\n", encoding="utf-8")
                return target_path

        # If not previously recorded, append new snapshot block separated by horizontal rule
        new_content = existing_content.rstrip() + "\n\n---\n\n" + snapshot_body + "\n"
        target_path.write_text(new_content, encoding="utf-8")
        return target_path

