#!/usr/bin/env python3
"""
Organizes research reports and logs from /Users/matthewhope/reports into
/Users/matthewhope/github_projects/portfolio-manager/reports following a clean 3-level hierarchy:
  reports/<SECURITY>/<SECURITY>_<YYYYMMDD>_<HHMMSS>/...
  reports/<SECURITY>/TradingAgentsStrategy_logs/...
  reports/<SECURITY>/Trading Agents Strategy_logs (symlink)

Also migrates historical Trade_Orders_*.md and 00_Portfolio_Actions_Dashboard.md
into /Users/matthewhope/github_projects/portfolio-manager/trade_orders.
"""

import os
import shutil
import sys
from pathlib import Path

SRC_VAULT = Path("/Users/matthewhope/reports")
SRC_REPORTS_DIR = SRC_VAULT / "reports"
SRC_RUNS_DIR = SRC_REPORTS_DIR / "reports"

REPO_ROOT = Path(__file__).resolve().parent.parent
DST_PORTFOLIO_DIR = REPO_ROOT / "portfolio"
DST_REPORTS_DIR = DST_PORTFOLIO_DIR / "01_agent_reports"
DST_TRADE_ORDERS_DIR = DST_PORTFOLIO_DIR / "00_trade_orders"


def get_dir_size(path: Path) -> int:
    """Recursively calculate total bytes of files in directory."""
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = Path(root) / f
            if fp.is_file() and not fp.is_symlink():
                total += fp.stat().st_size
    return total


def count_files(path: Path) -> int:
    """Recursively count all regular files in directory."""
    total = 0
    for root, _, files in os.walk(path):
        total += len(files)
    return total


def organize():
    print("=" * 60)
    print("🚀 STARTING REPORT & LOG REORGANIZATION")
    print("=" * 60)
    print(f"Source Vault       : {SRC_VAULT}")
    print(f"Destination Reports: {DST_REPORTS_DIR}")
    print(f"Destination Orders : {DST_TRADE_ORDERS_DIR}")

    DST_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    DST_TRADE_ORDERS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Migrate 316 Report Runs
    print("\n📦 1. Copying and organizing report run directories...")
    if not SRC_RUNS_DIR.exists():
        print(f"❌ Source runs dir not found: {SRC_RUNS_DIR}")
        sys.exit(1)

    report_run_dirs = sorted([d for d in SRC_RUNS_DIR.iterdir() if d.is_dir()])
    print(f"Found {len(report_run_dirs)} report run folders.")

    copied_reports = 0
    for run_dir in report_run_dirs:
        ticker = run_dir.name.split("_")[0].upper()
        ticker_dest = DST_REPORTS_DIR / ticker
        ticker_dest.mkdir(parents=True, exist_ok=True)

        dest_run_dir = ticker_dest / run_dir.name
        if dest_run_dir.exists():
            shutil.rmtree(dest_run_dir)

        shutil.copytree(run_dir, dest_run_dir, symlinks=True)
        copied_reports += 1

    print(f"✅ Successfully copied {copied_reports} report runs.")

    # 2. Migrate TradingAgentsStrategy_logs
    print("\n📦 2. Copying TradingAgentsStrategy_logs for each security...")
    ticker_log_dirs = sorted([d for d in SRC_REPORTS_DIR.iterdir() if d.is_dir() and d.name != "reports"])
    print(f"Found {len(ticker_log_dirs)} ticker directories with potential logs.")

    copied_logs = 0
    total_json_files = 0
    for tdir in ticker_log_dirs:
        src_log_dir = tdir / "TradingAgentsStrategy_logs"
        if src_log_dir.exists() and src_log_dir.is_dir():
            ticker_dest = DST_REPORTS_DIR / tdir.name.upper()
            ticker_dest.mkdir(parents=True, exist_ok=True)

            dst_log_dir = ticker_dest / "TradingAgentsStrategy_logs"
            if dst_log_dir.exists():
                shutil.rmtree(dst_log_dir)

            shutil.copytree(src_log_dir, dst_log_dir, symlinks=True)
            copied_logs += 1
            json_count = len(list(dst_log_dir.glob("*.json")))
            total_json_files += json_count

            # Create symlink with spaces for Obsidian and UI compatibility
            spaced_symlink = ticker_dest / "Trading Agents Strategy_logs"
            if spaced_symlink.exists() or spaced_symlink.is_symlink():
                spaced_symlink.unlink()
            spaced_symlink.symlink_to("TradingAgentsStrategy_logs", target_is_directory=True)

    print(f"✅ Successfully copied {copied_logs} log directories ({total_json_files} JSON files).")
    print(f"✅ Created symlinks for 'Trading Agents Strategy_logs' in all {copied_logs} security directories.")

    # 3. Migrate Trade Orders and Dashboard
    print("\n📦 3. Copying historical Trade_Orders and Dashboard...")
    trade_order_files = sorted(list(SRC_VAULT.glob("Trade_Orders_*.md")) + list(SRC_VAULT.glob("00_Portfolio_Actions_Dashboard.md")))
    copied_orders = 0
    for order_file in trade_order_files:
        dest_order = DST_TRADE_ORDERS_DIR / order_file.name
        shutil.copy2(order_file, dest_order)
        copied_orders += 1
        print(f"   Copied: {order_file.name} -> {DST_TRADE_ORDERS_DIR.name}/")

    print(f"✅ Successfully copied {copied_orders} trade order & dashboard files.")

    # 4. Rigorous Integrity Verification
    print("\n🔍 4. Performing full integrity verification...")
    src_total_files = sum(sum(len(files) for _, _, files in os.walk(d)) for d in report_run_dirs)
    dst_report_files = sum(sum(len(files) for _, _, files in os.walk(r))
                           for t in DST_REPORTS_DIR.iterdir() if t.is_dir()
                           for r in t.iterdir() if r.is_dir() and r.name != "TradingAgentsStrategy_logs" and not r.is_symlink())
    print(f"   Source report files: {src_total_files}")
    print(f"   Dest report files  : {dst_report_files}")
    assert src_total_files == dst_report_files, f"Mismatch in report file count: {src_total_files} vs {dst_report_files}"

    src_run_size = sum(get_dir_size(d) for d in report_run_dirs)
    dst_runs_size = sum(get_dir_size(r)
                        for t in DST_REPORTS_DIR.iterdir() if t.is_dir()
                        for r in t.iterdir() if r.is_dir() and r.name != "TradingAgentsStrategy_logs" and not r.is_symlink())
    print(f"   Source reports size: {src_run_size:,} bytes")
    print(f"   Dest reports size  : {dst_runs_size:,} bytes")
    assert src_run_size == dst_runs_size, f"Byte size mismatch: {src_run_size} vs {dst_runs_size}"

    src_logs_size = sum(get_dir_size(td / "TradingAgentsStrategy_logs")
                        for td in ticker_log_dirs if (td / "TradingAgentsStrategy_logs").exists())
    dst_logs_size = sum(get_dir_size(t / "TradingAgentsStrategy_logs")
                        for t in DST_REPORTS_DIR.iterdir() if t.is_dir() and (t / "TradingAgentsStrategy_logs").exists())
    print(f"   Source logs size   : {src_logs_size:,} bytes")
    print(f"   Dest logs size     : {dst_logs_size:,} bytes")
    assert src_logs_size == dst_logs_size, f"Log byte size mismatch: {src_logs_size} vs {dst_logs_size}"

    all_dest_tickers = sorted([d.name for d in DST_REPORTS_DIR.iterdir() if d.is_dir()])
    print(f"\n🎉 SUCCESS! All {copied_reports} reports and {copied_logs} log folders organized across {len(all_dest_tickers)} securities.")
    print("=" * 60)


if __name__ == "__main__":
    organize()
