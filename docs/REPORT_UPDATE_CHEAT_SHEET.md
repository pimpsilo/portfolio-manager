# 📋 Portfolio Manager (PM) — Report Update Cheat Sheet

A concise reference guide of terminal commands and workflows for updating **TradingAgents Analyst Research Reports** and generating **Trade Execution Order Reports (`Trade_Orders_YYYY-MM-DD.md`)**.

---

## ⚡ Quick Reference (TL;DR)

| Task | Command | Description |
| :--- | :--- | :--- |
| **Audit Status (Dry-Run)** | `python main.py --triage` | Audits vault vs. holdings; displays aging/missing reports (zero LLM cost) |
| **Update Stale Reports** | `python main.py --run-agents --older-than 3 --limit 5` | Evaluates up to 5 tickers whose reports are older than 3 days |
| **Update Specific Tickers** | `python main.py --run-agents --tickers AAPL,NVDA` | Forces fresh research reports for specified comma-separated symbols |
| **Full Sweep Refresh** | `python main.py --run-agents --all` | Re-evaluates all tracked tickers in the watchlist regardless of age |
| **Preview Trade Orders** | `python main.py --preview` | Calculates live rebalance allocations and prints CLI tables without writing |
| **Write Trade Orders** | `python main.py --execute` | Writes `Trade_Orders_YYYY-MM-DD.md` into your Obsidian vault directory |
| **End-to-End Pipeline** | `python main.py --pipeline --older-than 3 --limit 5 --execute` | Runs triage -> agent research -> solver -> writes trade orders in one step |
| **Auto-Watch Downloads** | `python main.py --watch` | Monitors `~/Downloads` for new broker CSV and auto-generates trade orders |

> [!NOTE]
> Ensure your virtual environment is active before running commands:
> ```bash
> source .venv/bin/activate
> ```

---

## 1. Auditing & Signal Triage (Zero-Cost / Dry-Run)

Use the triage engine to inspect which tickers have fresh reports, which are approaching stale (<=4 days remaining), and which are missing or expired (>14 days) without consuming API tokens.

### Full Watchlist & Holdings Audit
```bash
python main.py --triage
```

### Audit with Age Threshold
Filter to see which reports are older than $N$ days:
```bash
python main.py --triage --older-than 3
# or
python main.py --triage --older-than 7
```

### Audit Specific Category
```bash
python main.py --triage --category stocks_watchlist
```

---

## 2. Refreshing Analyst Research Reports (`TradingAgents`)

The multi-agent evaluation engine coordinates 5 analysts (Market, Fundamentals, News, Sentiment, Risk) to produce structured markdown research reports in `portfolio/01_agent_reports/<TICKER>/`.

### A. Incremental Age-Based Refresh (Recommended Daily Workflow)
Refreshes reports older than 3 days, capped to a batch size of 5:
```bash
python main.py --run-agents --older-than 3 --limit 5
```

### B. On-Demand Specific Tickers
Evaluate one or more specific securities immediately:
```bash
python main.py --run-agents --tickers AAPL,NVDA,CRM
```

### C. Weekly Tranche Refresh
Evaluate older reports with a larger batch limit:
```bash
python main.py --run-agents --older-than 7 --limit 10
```

### D. Complete Universe Sweep
Re-evaluate every security in the watchlist regardless of age:
```bash
python main.py --run-agents --all
# Aliases: --refresh-all, --force
```

### E. Using the Convenience Helper Script
You can also invoke the shell wrapper script directly:
```bash
./scripts/refresh_reports.sh --older-than 3 --limit 5
./scripts/refresh_reports.sh --tickers MSFT,AMZN
./scripts/refresh_reports.sh --all
```

---

## 3. Generating Trade Execution Orders (`Trade_Orders_YYYY-MM-DD.md`)

Once analyst reports are in place, the PM solver ingests broker holdings, parses latest agent signals, fetches live prices via `yfinance`, calculates correlation clusters, and determines whole-share order directives.

### A. Preview Rebalance Directives (CLI Output Only)
Runs the complete 5-step solver and displays verification tables in the terminal without modifying files:
```bash
python main.py --preview
# or simply:
python main.py
```

### B. Write Report to Obsidian Vault
Generates and writes `Trade_Orders_YYYY-MM-DD.md` into `portfolio/00_trade_orders/`:
```bash
python main.py --execute
```

### C. Override Broker CSV File
Point directly to a specific Fidelity portfolio positions CSV:
```bash
python main.py --execute --csv ~/Downloads/Portfolio_Positions_Sep-11-2026.csv
```

### D. Intra-Day Snapshots & Overwriting
When multiple extracts/downloads occur on the same date (e.g. `Portfolio_Positions_Sep-11-2026 (1).csv`, `(2).csv`, `(3).csv`), running `--execute` automatically appends intra-day snapshots to the existing daily report:
- **Intra-Day Snapshots (Default)**: Each run appends a timestamped snapshot header (`## ⏱️ Snapshot: {download_time} (Source: {source_file})`) separated by a `---` horizontal rule. Re-running the same CSV updates that snapshot in-place without duplicate bloat.
- **Force Overwrite (`--overwrite`)**: To overwrite the entire daily report from scratch with only the latest run:
  ```bash
  python main.py --execute --overwrite
  ```

### The Generated Report Sections
When written, `Trade_Orders_YYYY-MM-DD.md` contains 5 comprehensive sections:
1. **🎯 1. Immediate / Daily Actions**: Actionable rebalance orders with **Sell orders first (alphabetical by ticker)** followed by **Buy orders (alphabetical by ticker)**.
2. **⏳ 2. Aging / Stale Reports Summary**: Status table of all reports approaching stale or expired (>14 days). Filtered strictly to the control universe (`stocks.csv` + held positions; extraneous reports on disk are ignored).
3. **📊 3. Full Portfolio Rebalance & Drift Ledger**: Current shares, live quotes, target weights, dollar deltas, and drift/protection rationales.
4. **🌐 4. Non-Portfolio Securities with Active Agent Reports & Status**: All unheld watchlist securities with active reports, listing ratings, price targets, days left, and candidate status.
5. **📋 5. All Securities with Agent Reports**: Complete alphabetical directory (A–Z) of all securities with reports on file, including analyst verdicts, price targets, portfolio participation (`Held` vs `Watchlist`), and one-sentence core guidance summaries.
6. **🔗 Correlated Asset Clusters & Exposure**: Hierarchical correlation groups capped at max 25% exposure.


---

## 4. End-to-End Autonomous Pipeline

Run the entire pipeline sequentially: audits signals, runs LLM agent research on any needed tickers, solves the rebalance, and generates the final Obsidian trade order report.

```bash
# Autonomous daily pipeline (up to 5 oldest reports)
python main.py --pipeline --older-than 3 --limit 5 --execute

# Targeted pipeline for specific tickers
python main.py --pipeline --tickers NVDA,MU --execute
```

---

## 5. Downloads Watcher & Automation Daemons

Automate report generation whenever you export a new `Portfolio_Positions_*.csv` from Fidelity to your `~/Downloads` folder.

### Run Watcher in Foreground
Monitors `~/Downloads` every 3 seconds for new CSV downloads:
```bash
python main.py --watch
# or via wrapper:
./scripts/run_watcher.sh
```

### Install Background Service (macOS `launchd`)
Installs and activates a persistent background daemon that runs continuously across restarts:
```bash
./scripts/install_service.sh
```

To stop and remove the background daemon:
```bash
./scripts/uninstall_service.sh
```

To check daemon status:
```bash
launchctl list | grep portfoliomanager
tail -f logs/watcher.log
```

---

## 6. CLI Argument Reference Matrix

| Flag / Option | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `--triage` | Flag | `False` | Run dry-run audit of watchlist & holdings vs reports |
| `--run-agents` | Flag | `False` | Execute TradingAgents evaluation on queued tickers |
| `--pipeline` | Flag | `False` | Run end-to-end: Triage -> Agents -> Solver -> Trade Orders |
| `--tickers` | String | `None` | Comma-separated list of symbols (e.g. `AAPL,NVDA,GOOG`) |
| `--limit` | Integer | `5` | Maximum number of tickers to evaluate in this batch |
| `--older-than`, `--days` | Integer | `None` | Re-evaluate reports older than $N$ days (missing always included) |
| `--all`, `--refresh-all` | Flag | `False` | Re-evaluate ALL reports regardless of age (equivalent to `--older-than 0`) |
| `--category` | String | `None` | Filter triage/agents to category (e.g. `stocks_watchlist`) |
| `--preview` | Flag | `True` | Display rebalance verification tables without writing files |
| `--execute` | Flag | `False` | Write `Trade_Orders_YYYY-MM-DD.md` to Obsidian vault |
| `--overwrite` | Flag | `False` | Overwrite existing daily report instead of appending a new snapshot |
| `--csv` | Path | `None` | Explicit path to broker CSV (defaults to newest in `~/Downloads`) |
| `--config` | Path | `config.yaml`| Custom path to `config.yaml` configuration file |
| `--watch` | Flag | `False` | Monitor `~/Downloads` for new CSV and auto-execute |

---

## 7. Troubleshooting & Environment Tips

- **Missing Environment Variables**:
  Make sure `.env` contains your Google Gemini API key:
  ```bash
  GEMINI_API_KEY="your-api-key-here"
  ```
- **Rate Limit Delay**:
  Batch runs include a built-in 5.0-second delay between tickers (configured in `config.yaml` under `watchlist.rate_limit_delay_seconds`) to avoid API quota throttling.
- **Deduplication**:
  Dual-class shares (`GOOG`/`GOOGL`, `FOX`/`FOXA`) are automatically aliased to whichever class is currently held in your broker account.
