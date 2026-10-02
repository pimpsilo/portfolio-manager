# 📋 Portfolio Manager (PM) — Report Update Cheat Sheet

A concise reference guide of terminal commands, workflows, and governance rules for updating **TradingAgents Analyst Research Reports** and generating **Trade Execution Order Reports (`Trade_Orders_YYYY-MM-DD.md`)**.

---

## ⚡ Quick Reference (TL;DR)

| Task | Command | Description |
| :--- | :--- | :--- |
| **Audit Status (Dry-Run)** | `python main.py --triage` | Audits vault vs. holdings; displays aging/missing reports (zero LLM cost) |
| **Show Only Stale / Queue** | `python main.py --stale` | Displays only the queue of reports needing refresh (hides fresh reports) |
| **Update Stale Reports** | `python main.py --run-agents --older-than 3 --limit 5` | Evaluates up to 5 tickers whose reports are older than 3 days |
| **Update Specific Tickers** | `python main.py --run-agents --tickers AAPL,NVDA` | Forces fresh research reports for specified comma-separated symbols |
| **Full Sweep Refresh** | `python main.py --run-agents --all` | Re-evaluates all tracked tickers in the watchlist regardless of age |
| **Preview Trade Orders** | `python main.py --preview` | Calculates live rebalance allocations and prints CLI tables without writing |
| **Write Trade Orders** | `python main.py --execute` | Writes `Trade_Orders_YYYY-MM-DD.md` into your Obsidian vault directory |
| **End-to-End Pipeline** | `python main.py --pipeline --older-than 3 --limit 5 --execute` | Runs triage -> agent research -> solver -> writes trade orders in one step |
| **Auto-Watch Downloads** | `python main.py --watch` | Monitors `~/Downloads` for new broker CSV and auto-generates trade orders |
| **Validate Configuration** | `python main.py --validate-config` | Verifies YAML syntax, paths, and clustering parameter bounds |
| **Historical Backtest** | `python main.py --backtest scratch/prices.csv` | Sweeps parameter grid against price CSV (Sharpe, Drawdown, Turnover) |

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

### Show Only Reports in Need of Refresh (Hide Fresh Reports)
```bash
python main.py --stale
# Aliases: --queue, --stale-only, --queue-only
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

The multi-agent evaluation engine coordinates 5 analysts (Market, Fundamentals, News, Sentiment, Risk) to produce structured markdown research reports in `01_agent_reports/<TICKER>/` within the Portfolio vault (`/Users/matthewhope/Library/Mobile Documents/iCloud~md~obsidian/Documents/Portfolio/01_agent_reports/`).

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

### E. Explicit Settled Trade Date
Target an explicit historical market trade date (defaults to the latest settled trading day):
```bash
python main.py --run-agents --tickers AAPL --trade-date 2026-09-30
```

### F. Using the Convenience Helper Script
You can also invoke the shell wrapper script directly:
```bash
./scripts/refresh_reports.sh --older-than 3 --limit 5
./scripts/refresh_reports.sh --tickers MSFT,AMZN
./scripts/refresh_reports.sh --all
```

---

## 3. Generating Trade Execution Orders (`Trade_Orders_YYYY-MM-DD.md`)

Once analyst reports are in place, the PM solver ingests broker holdings, parses latest agent signals, fetches live prices via `yfinance`, calculates correlation clusters with intra-cluster concentration controls ($n \le 4$), applies portfolio safeguards, and determines whole-share order directives.

### A. Preview Rebalance Directives (CLI Output Only)
Runs the complete deterministic solver and displays verification tables in the terminal without modifying files:
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

### E. Daily Pointer File Auto-Sync
Every successful `--execute` run automatically updates the daily pointer file (`_trade_order_latest.txt` at `/Users/matthewhope/Library/Mobile Documents/iCloud~md~obsidian/Documents/Matt Hope/Daily/_trade_order_latest.txt`). This allows Obsidian daily dashboard widgets to dynamically embed the latest trade directives.

### The Generated Report Structure
When written, `Trade_Orders_YYYY-MM-DD.md` contains structured executive summaries and 3 primary sections with clean collapsible `<details>` sub-tables:

1. **Header & Decision / Data Quality Block**:
   - **Portfolio Financials**: Live portfolio value, cash balances, cash %, projected ending cash after orders.
   - **Provenance Metadata**: Source export CSV, download timestamp, solver run timestamp.
   - **Decision & Data Quality**: Operational status (`READY FOR REVIEW` vs `REVIEW REQUIRED`), total orders (sells/buys), active research coverage (`XX/YY current`), post-trade cash %, target equity %, market-cap source breakdown, binding constraints, and any Safe Mode warnings.

2. **🎯 1. Immediate / Daily Actions**:
   - Actionable rebalance orders with **Sell orders first (alphabetical by ticker)** followed by **Buy orders (alphabetical by ticker)**.
   - Clickable markdown report links with inline ~8pt report date badges (`[TICKER](../01_agent_reports/...) YYYY-MM-DD`).
   - Current shares, real-time price, target weight, target value, dollar delta, order shares, and directive rationales.

3. **⏳ 2. Reports Summary** (Collapsible Sub-Tables):
   - **Aging / Stale Reports Summary**: Status table of all reports approaching stale (<=4 days) or expired (>14 days), strictly scoped to the control universe (`stocks.csv` + held positions).
   - **Non-Portfolio Securities with Active Agent Reports & Status**: All unheld watchlist securities with active reports, listing ratings, price targets, days left, and entry status.
   - **Securities without Active Agent Reports**: All securities of interest (portfolio holdings + `stocks.csv` watchlist) that lack an active report (missing or expired >14d).
   - **All Securities with Agent Reports**: Complete alphabetical directory (A–Z) of all securities with reports on file, including analyst verdicts, price targets, portfolio participation (`Held` vs `Watchlist`), and one-sentence core guidance summaries.

4. **📊 3.Portfolio** (Collapsible Sub-Tables):
   - **Full Portfolio Rebalance & Drift Ledger**: Current shares, live quotes, target weights, dollar deltas, whole-share orders, and drift/protection rationales for all held assets and active candidates.
   - **Correlated Asset Clusters & Exposure**: Hierarchical clusters ($k=10$) showing Cluster ID, Anchor Lead (with its target weight %), Group Assets, Intra-Cluster Correlation, Combined Target Weight %, Cap Limit (25%), and Status (`✅ OK` / `⚠️ CAPPED`).

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

### Restart Background Service
Restarts the daemon to pick up configuration or code updates:
```bash
./scripts/restart_service.sh
```

### Stop & Uninstall Background Service
```bash
./scripts/uninstall_service.sh
```

### Check Daemon Status & Logs
```bash
launchctl list | grep portfoliomanager
tail -f logs/watcher.log
```

---

## 6. System Safeguards, Governance & Mathematical Rules

The PM engine enforces deterministic mathematical rules and risk constraints to guarantee safe capital allocation:

1. **Correlation Clustering Engine & $n \le 4$ Concentration**:
   - **Metric & Linkage**: Aligned with `Correlation_Analyzer` using Pearson distance $D = \text{clip}(1 - r, 0, 2)$ with average linkage on 6 months of returns.
   - **Target Clusters**: Flat clusters generated via `scipy.cluster.hierarchy.fcluster(criterion='maxclust', t=10)`.
   - **Cluster Cap**: Maximum 25% cumulative exposure per correlated group (`risk.max_cluster_exposure: 0.25`).
   - **$n \le 4$ Intra-Cluster Rationalization**: Limits each cluster to at most 4 high-conviction holdings (`risk.max_assets_per_cluster: 4`).
   - **Composite Conviction Guide**: Candidates in oversized clusters are ranked by:
     $$\text{Conviction Score} = \text{Market Cap Tier Multiplier} + \text{6-Month Sharpe Ratio} + \text{Analyst Upside \%}$$
   - **Graceful Liquidation**: Pruned tickers in oversized clusters route to `SignalType.AVOID` with trade rationale `RATIONALIZED_CLUSTER_CAP`, triggering complete 100% liquidation.

2. **Hard 15% Cash Reserve Floor ("Dry Powder")**:
   - Enforces a minimum 15% cash reserve (`risk.min_cash_reserve: 0.15`, `enforce_cash_reserve_on_buys: true`).
   - Buy order sizing is strictly constrained so that total allocated capital never draws cash below the 15% reserve floor.

3. **Staged Transaction Stepping & Dollar Caps**:
   - **50% Tranches**: Starter entries and rebalance adjustments execute in staged 50% tranches (`starter_step_factor: 0.50`, `rebalance_step_factor: 0.50`) to avoid chasing price spikes.
   - **$6,000 Trade Cap**: Standard orders are capped at a maximum of $6,000 per order (`max_trade_dollar_cap: 6000.0`).
   - **Immediate Liquidation**: `AVOID` signals bypass stepping and execute 100% full liquidation (`avoid_step_factor: 1.00`).

4. **Asymmetric "Let Winners Run" Drift Policy**:
   - Standard 20% relative drift trigger for trims.
   - Equal-weight `HOLD` positions can float up to **+100% above target weight** (`hold_drift_tolerance: 1.00`) before triggering a rebalance trim, preventing premature profit-taking on momentum winners.

5. **Option 5 Market-Cap Anchoring**:
   - Base anchor multipliers: **Mega-Cap ($200B+)** = 3.0x, **Large-Cap ($50B–$200B)** = 1.8x, **Mid/Emerging (<$50B)** = 1.0x.
   - Active signal multipliers: `OVERWEIGHT` (1.5x), `EQUAL_WEIGHT` (1.0x), `UNDERWEIGHT` (0.5x), `AVOID` (0.0x).
   - Position limit: Hard $\le 15\%$ maximum single stock exposure.

6. **Dual-Class Share Aliasing**:
   - Automatically unifies dual-class share symbols (`GOOG`/`GOOGL`, `FOX`/`FOXA`, `BRK.A`/`BRK.B`) to whichever share class is currently held in your brokerage account to prevent duplicate allocation.

7. **Control Universe Scoping & Blacklist**:
   - The investable universe is strictly bounded by `stocks.csv` plus held broker positions.
   - Blacklisted symbols (e.g. `GEV` in `config.yaml`) are permanently excluded from universe selection and capital allocation.

8. **Data Quality & Safe Mode**:
   - Live prices fetched via `yfinance` with sanity checks against stale prices, negative quotes, and anomalies.
   - If market data quality degrades, **Safe Mode** automatically engages: buy directives are suppressed, existing positions are preserved, and detailed warning callouts are displayed.

---

## 7. Configuration Validation & Historical Backtesting CLI

### Validate Configuration
Check configuration syntax, filesystem paths, and risk parameter bounds without loading broker data:
```bash
python main.py --validate-config
```

### Parameter Sweep Backtest
Evaluate cash-reserve, cluster, and position-cap variants against historical price series:
```bash
python main.py --backtest scratch/backtest_prices.csv --backtest-output scratch/backtest_results.csv
```
The backtest engine computes comparative analytics including:
- Total Return (%) & Annualized Return (%)
- Annualized Volatility (%)
- Sharpe Ratio & Sortino Ratio
- Maximum Drawdown (%)
- Portfolio Turnover & Total Rebalance Count

---

## 8. Complete CLI Argument Reference Matrix

| Flag / Option | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `--triage` | Flag | `False` | Run dry-run audit of watchlist & holdings vs reports (zero LLM cost) |
| `--stale`, `--queue` | Flag | `False` | Display only the queue of reports needing refresh (hides fresh reports) |
| `--run-agents` | Flag | `False` | Execute TradingAgents evaluation on queued or specified tickers |
| `--pipeline` | Flag | `False` | Run end-to-end: Triage -> Agents -> Solver -> Trade Orders |
| `--tickers` | String | `None` | Comma-separated list of symbols to evaluate (e.g. `AAPL,NVDA,GOOG`) |
| `--limit` | Integer | `5` | Maximum number of tickers to evaluate in this batch |
| `--older-than`, `--days` | Integer | `None` | Re-evaluate reports older than $N$ days (missing always included) |
| `--all`, `--refresh-all` | Flag | `False` | Re-evaluate ALL reports regardless of age (equivalent to `--older-than 0`) |
| `--trade-date` | String | `None` | Explicit trade date (`YYYY-MM-DD`); defaults to latest settled trading day |
| `--category` | String | `None` | Filter triage/agents to category (e.g. `stocks_watchlist`) |
| `--preview` | Flag | `True` | Display rebalance verification tables without writing files |
| `--execute` | Flag | `False` | Write `Trade_Orders_YYYY-MM-DD.md` into Obsidian vault |
| `--overwrite` | Flag | `False` | Overwrite existing daily report instead of appending a new snapshot |
| `--csv` | Path | `None` | Explicit path to broker CSV (defaults to newest in `~/Downloads`) |
| `--config` | Path | `config.yaml`| Custom path to `config.yaml` configuration file |
| `--watch` | Flag | `False` | Monitor `~/Downloads` for new CSV and auto-execute |
| `--validate-config` | Flag | `False` | Validate configuration syntax, paths, and parameter bounds, then exit |
| `--backtest` | Path | `None` | Run parameter evaluation sweep against historical price CSV |
| `--backtest-output` | Path | `None` | Output CSV path to save backtest sweep results |

---

## 9. Troubleshooting & Environment Tips

- **Missing Environment Variables**:
  Make sure `.env` contains your Google Gemini API key:
  ```bash
  GEMINI_API_KEY="your-api-key-here"
  ```
- **Rate Limit Delay**:
  Batch agent evaluations include a built-in 5.0-second delay between tickers (configured in `config.yaml` under `watchlist.rate_limit_delay_seconds`) to avoid Gemini API quota throttling.
- **Real-Time Daemon Logs**:
  The `launchd` service plist runs with `PYTHONUNBUFFERED=1`, allowing real-time log inspection via:
  ```bash
  tail -f logs/watcher.log
  ```
- **Deduplication & Share Classes**:
  Dual-class shares (`GOOG`/`GOOGL`, `FOX`/`FOXA`, `BRK.A`/`BRK.B`) are automatically aliased to whichever class is currently held in your broker account.
