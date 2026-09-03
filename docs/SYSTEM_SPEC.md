# AGY System Prompt: Portfolio Manager (PM) Engine v2.0

## 1. System Objective
Develop and maintain a deterministic **Portfolio Manager (PM) Engine** to close the control loop between asset-level evaluations (`tradingagents` markdown reports) and capital allocation. The engine acts as a mathematical feedback controller: it ingests categorical signals from Markdown files, compares them against the current portfolio state from a broker CSV, fetches real-time market prices, applies mathematical risk constraints, and outputs specific, actionable whole-share trade deltas into an Obsidian vault.

---

## 2. Execution Environment & Tooling
* **Framework:** Google Antigravity (AGY) / Python 3.11+.
* **Data Access:** Local filesystem (MCP or native Python) to read Markdown signal reports and Fidelity CSV exports.
* **Core Principle:** Strictly deterministic Python code executes all mathematical optimization, hierarchical clustering, real-time pricing, risk constraints, and reconciliation tasks. The LLM orchestrates data flow, summaries, and user interaction, but does **not** compute portfolio weights, risk caps, or share sizes.

---

## 3. Data Ingestion & Cleaning Requirements

### A. Markdown Signal Parsing (`pm/ingest/markdown_signals.py`)
* **Source:** `/Users/matthewhope/reports/` (recursively scan subdirectories for `*.md`, specifically `5_portfolio/decision.md`, `complete_report.md`, and `00_Portfolio_Actions_Dashboard.md`).
* **Extraction:**
  - `Ticker`: Extracted from folder regex `^([A-Z0-9]+)_(\d{8})_(\d{6})$` or file text.
  - `Signal`: Normalized to `OVERWEIGHT` (1.5x), `EQUAL_WEIGHT` (1.0x), `UNDERWEIGHT` (0.5x), `AVOID` (0.0x).
  - `Date`: Parsed from folder timestamp (`YYYYMMDD`) or frontmatter.
  - `Price Target` & `Stop Loss`: Parsed if present.
* **14-Day Expiration Filter (`max_age_days = 14`):**
  - A report is expired if $(\text{Current Date} - \text{Report Date}) > 14 \text{ days}$.
  - **Deduplication:** When multiple historical runs exist for a ticker, select strictly the **newest** report. Older reports are marked superseded.
  - **Held Assets with Expired Reports:** Default to neutral `EQUAL_WEIGHT` (1.0x). (Never panic-sell an existing holding just because an analyst run is overdue).
  - **Non-Portfolio Candidates with Expired Reports:** Excluded from the investable universe.

### B. Broker CSV Parsing (`pm/ingest/broker_csv.py`)
* **Source:** Auto-locates the newest `Portfolio_Positions_*.csv` in `/Users/matthewhope/Downloads/` or configured folder.
* **Cleaning Rules:**
  - **Cash Extraction:** Identifies `Symbol == "FDRXX**"`, `SPAXX`, `FCASH`, or `Description` containing `"HELD IN MONEY MARKET"`. Maps `Current value` to `cash_balance`.
  - **Ticker Formatting:** Strips trailing `**`, whitespace, and quotes from `Symbol`.
  - **Numeric Casting:** Strips `$`, `,`, and `%` from `Last price`, `Current value`, `Quantity`, and `Cost basis`, casting to `float`.
  - **Row Filtering:** Drops uninvestable rows (blank symbols, `"Pending Activity"`, `"Account Total"`, and brokerage legal disclaimers).
  - **Quantity Preservation:** Preserves existing fractional share quantities (e.g. `15.004` shares of AAPL).

### C. Investable Universe Formation
The total universe evaluated by the solver is the union of:
$$\text{Universe} = \text{Current Portfolio Holdings} \cup \{ \text{Non-Portfolio Candidates with active OVERWEIGHT signals} \}$$
*(Non-portfolio candidates with `Hold` or `Underweight` ratings remain on the watchlist and are never initiated as new starter positions).*

---

## 4. 5-Step Deterministic Mathematical & Risk Solver

### Step 1: Real-Time Pricing (`pm/market_data/pricing.py`)
* Use `yfinance.Tickers` to retrieve live quotes (`fast_info.last_price` / regular market price) for all tickers in the universe.
* Override stale CSV `Last price` with live market data.
* Recalculate total portfolio live value:
  $$\text{Total Live Value} = \sum (\text{Current Shares}_i \times P_{\text{realtime}, i}) + \text{Cash Balance}$$
* If `yfinance` network times out for an existing holding, fall back gracefully to the CSV price with a warning log.

### Step 2: Correlation Filtering & Clustering Engine (`pm/risk/clustering.py`)
* Download 6 months of daily adjusted close prices for all universe tickers.
* Compute daily percentage returns $R$ and correlation matrix $C = \text{Corr}(R)$.
* Convert to angular distance metric:
  $$D = \sqrt{\text{clip}(0.5 \times (1.0 - C), 0.0, 1.0)}$$
* Execute hierarchical clustering using `scipy.cluster.hierarchy.linkage` (average or ward method on condensed distance matrix `squareform(D)`).
* Form flat clusters using `fcluster(Z, t=0.5, criterion='distance')`.
* Enforce a **maximum 25% exposure cap** per correlated cluster group.

### Step 3 & 4: Base Weighting & Hard Constraints Optimizer (`pm/risk/constraints.py`)
* **Signal Multipliers:**
  - `OVERWEIGHT`: 1.5x
  - `EQUAL_WEIGHT`: 1.0x
  - `UNDERWEIGHT`: 0.5x
  - `AVOID`: 0.0x
* **Hard Portfolio Limits:**
  - Maximum single position size: **$\le 15\%$** of total portfolio.
  - Minimum cash reserve floor: **$\ge 10\%$** of total portfolio ($\text{Equity Budget} \le 90\%$).
  - Maximum cluster exposure: **$\le 25\%$** cumulative per correlated cluster.
* **Iterative Proportional Re-normalization:**
  - Assets with `AVOID` are locked at $0.0\%$.
  - Excess weight from single position caps ($> 15\%$) or cluster caps ($> 25\%$) is trimmed and iteratively redistributed to uncapped, non-avoid assets.
  - Guarantees all constraints are strictly satisfied and $\sum W_{\text{equity}} \le 90\%$.

### Step 5: Recalibrated Reconciliation & Order Sizing (`pm/engine/reconciler.py`)
Reconcile target weights against current holdings using **Option B (Relative Drift + Minimum Trade Floor)** and **Whole-Share Prioritization**:

1. **Calculate Values:**
   - $V_{\text{target}} = W_{\text{target}} \times \text{Total Portfolio Value}$
   - $V_{\text{current}} = \text{Current Shares} \times P_{\text{realtime}}$
   - $\Delta\$ = V_{\text{target}} - V_{\text{current}}$
   - $\text{Relative Drift} = \frac{|\Delta\$|}{V_{\text{target}}}$ (for $V_{\text{target}} > 0$)

2. **Action Classification:**
   - **Full Liquidation (`AVOID` signal or $W_{\text{target}} = 0$):**
     If $\text{Current Shares} > 0$, immediately generate `SELL` for **100% of current shares**. Preserve exact fractional quantity (e.g. `SELL 15.004 sh`) to leave zero account stub.
   - **New Starter Entry ($\text{Current Shares} == 0$):**
     If $V_{\text{target}} \ge \$1,500$, generate `BUY` order.
   - **Existing Holding Rebalance:**
     Trigger order if and only if **BOTH** criteria are met:
     1. $|\Delta\$| \ge \mathbf{\$1,500}$ (Minimum Trade Floor to eliminate sub-$1,000 churn).
     2. $\text{Relative Drift} > \mathbf{20\%}$ (Relative deviation from target size).
     Otherwise: `HOLD / NO_TRADE` (suppressed within deadband).

3. **Whole-Share Sizing Rules:**
   - For all standard buys and trims, round order size to integer whole shares:
     $$\text{Order Shares} = \text{round}\left(\frac{|\Delta\$|}{P_{\text{realtime}}}\right)$$
   - Built-in cash safety: Floor shares ($\lfloor \frac{|\Delta\$|}{P} \rfloor$) if rounding up would violate the 10% cash reserve or position cap.
   - If $|\Delta\$| / P < 0.5$ (share size rounds to 0), suppress to `HOLD` with reason `SUB_SHARE_MINIMUM`.

---

## 5. Output Directives & Obsidian Reporting (`pm/output/reporter.py`)
Generate a structured Markdown document and write it to the Obsidian vault as:
`/Users/matthewhope/reports/Trade_Orders_YYYY-MM-DD.md`

### Required Format & Structure:
1. **Header Block:**
   `# Trade Execution Orders — YYYY-MM-DD`
   Summary callout with Live Portfolio Value, Current Cash ($ and %), and Projected Ending Cash ($ and %).
2. **Actionable Directives Table (Trims/Sells first, then Buys):**
   ```markdown
   | Ticker | Current Shares | Current Price (yfinance) | Target Weight | Target Value | Delta ($) | Action | Order Shares |
   | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
   | **[[META]]** | 30 | $610.68 | 2.73% | $10,047.37 | $-8,273.03 | **SELL** 🔴 | **14** |
   | **[[ADBE]]** | 0 | $285.75 | 2.73% | $10,047.37 | $+10,047.37 | **BUY** 🟢 | **35** |
   ```
3. **Full Portfolio Rebalance & Drift Ledger:**
   Complete ledger of all assets including `HOLD 🟡` positions.
4. **Correlated Clusters Exposure Table:**
   Cluster ID, Member Assets (wikilinked), Combined Target Weight %, Cap Limit (25.00%), and Status (`✅ OK`).

---

## 6. Verification & Execution CLI
* **Preview Mode:** `python main.py --preview` (ingests, prices, clusters, and displays formatted verification tables in terminal without writing).
* **Execution Mode:** `python main.py --execute` (generates and overwrites `Trade_Orders_YYYY-MM-DD.md` in Obsidian vault).
* **Test Suite:** `pytest tests -v` (100% pass rate across ingestion, clustering, constraints, and reconciler).
