import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Union

from pm.engine.controller import PortfolioManagerEngine
from pm.models import ReconciliationSummary
from pm.output.reporter import MarkdownTradeReporter

logger = logging.getLogger(__name__)


class DownloadWatcher:
    """
    Monitors ~/Downloads for new or updated Fidelity Portfolio_Positions_*.csv files
    and automatically executes the Portfolio Manager solver to refresh Obsidian trade orders.
    """

    def __init__(self, config_path: str = "config.yaml"):
        self.engine = PortfolioManagerEngine(config_path=config_path)
        self.downloads_dir = Path(self.engine.downloads_dir)
        output_cfg = self.engine.config.get("output", {})
        self.reporter = MarkdownTradeReporter(
            self.engine.obsidian_vault_dir,
            link_style=output_cfg.get("report_link_style", "markdown"),
            prefer_complete_report=output_cfg.get("prefer_complete_report", True),
            date_layout=output_cfg.get("date_layout", "stacked"),
            append_daily_snapshots=output_cfg.get("append_daily_snapshots", True),
        )

        watcher_cfg = self.engine.config.get("watcher", {})
        self.poll_interval = watcher_cfg.get("poll_interval_seconds", 3)
        self.debounce_seconds = watcher_cfg.get("debounce_seconds", 2)

        self._last_seen_file: Optional[Path] = None
        self._last_seen_mtime: float = 0.0

    def get_latest_csv(self) -> Optional[Path]:
        if not self.downloads_dir.exists():
            return None
        csv_files = list(self.downloads_dir.glob("Portfolio_Positions_*.csv"))
        if not csv_files:
            return None
        csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return csv_files[0]

    def process_file(
        self,
        csv_path: Union[str, Path],
        overwrite: bool = False,
    ) -> Tuple[ReconciliationSummary, Path]:
        """
        Executes the Portfolio Manager solver for a broker CSV file and writes/appends
        the trade orders markdown report in the configured Obsidian vault.

        Returns:
            Tuple of (ReconciliationSummary, Path to generated report markdown file)
        """
        path_obj = Path(csv_path)
        summary = self.engine.run_solver(csv_path=str(path_obj))
        out_path = self.reporter.write_report(summary, overwrite=overwrite)
        return summary, out_path

    def run(self):
        print("=======================================================")
        print("   👀 PORTFOLIO MANAGER — DOWNLOAD WATCHER ACTIVE     ")
        print("=======================================================")
        print(f"Monitoring folder : {self.downloads_dir}")
        print(f"Polling frequency : Every {self.poll_interval} seconds")
        print(f"Output Vault      : {self.engine.obsidian_vault_dir}")
        print("Export a new Portfolio_Positions_*.csv from Fidelity to trigger auto-rebalance.")
        print("Press Ctrl+C to stop.\n")

        initial_csv = self.get_latest_csv()
        if initial_csv:
            self._last_seen_file = initial_csv
            self._last_seen_mtime = initial_csv.stat().st_mtime
            print(f"Baseline CSV established: {initial_csv.name} (modified: {datetime.fromtimestamp(self._last_seen_mtime).strftime('%Y-%m-%d %H:%M:%S')})")
        else:
            print("No existing Portfolio_Positions_*.csv found yet. Waiting for first download...")

        while True:
            try:
                time.sleep(self.poll_interval)
                current_csv = self.get_latest_csv()
                if not current_csv:
                    continue

                current_mtime = current_csv.stat().st_mtime

                is_new_file = self._last_seen_file is None or current_csv.name != self._last_seen_file.name
                is_modified = current_mtime > (self._last_seen_mtime + 0.1)

                if is_new_file or is_modified:
                    now_str = datetime.now().strftime("%H:%M:%S")
                    print(f"\n⚡ [{now_str}] New/Updated broker CSV detected: {current_csv.name}")
                    print(f"   Waiting {self.debounce_seconds}s for browser download completion...")
                    time.sleep(self.debounce_seconds)

                    self._last_seen_file = current_csv
                    self._last_seen_mtime = current_csv.stat().st_mtime

                    print("🚀 Executing Portfolio Manager Solver...")
                    summary, out_path = self.process_file(current_csv)

                    active_count = len([a for a in summary.allocations if a.action in ("BUY", "SELL") and a.order_shares > 0])
                    print(f"✅ Success! Rebalancing complete ({active_count} actionable trade orders).")
                    print(f"📝 Updated Obsidian Vault: {out_path.name}")
                    print(f"   Live Portfolio Value: ${summary.total_portfolio_value:,.2f} | Cash: ${summary.current_cash:,.2f}\n")
                    print("Listening for next download...")

            except KeyboardInterrupt:
                print("\n🛑 Watcher stopped by user.")
                sys.exit(0)
            except Exception as e:
                print(f"⚠️ Error during auto-rebalance: {e}")
                time.sleep(self.poll_interval)


if __name__ == "__main__":
    watcher = DownloadWatcher()
    watcher.run()
