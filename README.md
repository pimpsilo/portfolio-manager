# Portfolio Manager (PM) Engine

A deterministic feedback controller closing the control loop between asset-level evaluations (`tradingagents` markdown reports) and capital allocation (`Portfolio_Positions_*.csv`).

## 🚀 Key Features

1. **Deterministic Data Ingestion**:
   - Recursively parses Obsidian signal vaults (`/Users/matthewhope/Library/Mobile Documents/iCloud~md~obsidian/Documents/Portfolio/01_agent_reports/`), extracting signals, dates, price targets, and stop losses.
   - Parses and cleans Fidelity broker CSV files (`Portfolio_Positions_*.csv`), extracting cash balances (e.g. `FDRXX`) and stripping symbol artifacts.
   - Applies a configurable expiration filter (default: 14 days) and considers non-portfolio candidate assets for inclusion.

2. **5-Step Mathematical Solver**:
   - **Step 1: Real-Time Pricing**: Fetches live quotes via `yfinance` to override stale CSV prices.
   - **Step 2: Correlation Clustering**: Computes pairwise return correlation and generates hierarchical clusters via `scipy` to enforce maximum cluster exposure limits (max 25%).
   - **Step 3: Base Weight Multipliers**: Overweight (1.5x), Equal-weight (1.0x), Underweight (0.5x), Avoid (0.0x).
   - **Step 4: Hard Constraints**: Single position cap (15%), minimum cash reserve (10%), and iterative re-normalization.
   - **Step 5: Recalibrated Rebalance (Option B)**: Enforces a 20% relative position drift threshold combined with a $1,500 minimum trade floor to eliminate market noise.

3. **Whole-Share Prioritization**:
   - Standard orders round to whole integer shares.
   - Exact fractional precision is strictly reserved for complete 100% position liquidations.

4. **Obsidian Vault Reporting**:
   - Outputs markdown reports directly to your Obsidian vault as `Trade_Orders_YYYY-MM-DD.md`.

## 📋 Cheat Sheet & Workflows

For a quick copy-pasteable reference of all commands to update analyst research reports, audit signal freshness, and generate trade execution orders, see:
👉 **[Report Update Cheat Sheet](docs/REPORT_UPDATE_CHEAT_SHEET.md)**

---

## 🛠️ Usage

### 1. Environment Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Preview Rebalancing Directives
```bash
python main.py --preview
```

### 3. Generate Trade Orders in Obsidian Vault
```bash
python main.py --execute
```

### 4. Run Unit Tests
```bash
pytest tests -v
```

### Configuration and data-quality checks

Validate paths and risk settings without loading a broker export:

```bash
python main.py --validate-config
```

Solver runs surface data-quality warnings when market data falls back to the broker
export or when market-cap data is unavailable. If a held position has no usable
price, the run stops instead of generating unsafe order sizes; missing market-cap
data uses neutral weighting and suppresses new buys.

### Parameter evaluation

Evaluate allocation parameter variants against a cached historical price CSV:

```bash
python main.py --backtest scratch/backtest_prices.csv \
  --backtest-output scratch/backtest_results.csv
```

The sweep compares cash-reserve and position-cap variants using total return,
annualized return and volatility, Sharpe/Sortino, maximum drawdown, turnover,
and rebalance count. It is an evaluation aid, not a prediction or trade
recommendation.
