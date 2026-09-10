# Trade Order Report Links — Implementation Plan

**Target:** `pm/output/reporter.py` (with small supporting changes in `pm/models.py` and `pm/engine/controller.py`)
**Goal:** Every ticker the reporter prints links to that ticker's current agent report, in the format used across `00_trade_orders/Trade_Orders_*.md`:

```
[TICKER](../01_agent_reports/<TICKER>/<RUN>/complete_report.md)
```

Applies to all four tables: **Execution Directives**, **Aging & Approaching Stale Reports**, **Full Portfolio Rebalance & Drift Ledger**, and **Correlated Asset Clusters & Exposure**.

---

## 1. Why Markdown links, not wikilinks

- **Pipe-free.** Inside a Markdown table, `|` is a cell delimiter. A wikilink alias (`[[path|TICKER]]`) must be escaped as `[[path\|TICKER]]`, which is fragile — an auto-formatter can strip the backslash and shatter the row (this already happened to the 2026-09-10 note). `[TICKER](path)` contains no `|`, so it can never break a table.
- **Portable.** Renders as a real hyperlink in Obsidian *and* in exported/published Markdown.
- The path is made **relative to the output directory** so it works regardless of where the vault lives on disk.

---

## 2. Where the report path comes from

`pm/ingest/markdown_signals.py` already records the source of each parsed signal:

```python
ParsedSignal.source_path  # absolute path to <RUN>/5_portfolio/decision.md (or complete_report.md)
```

`ParsedSignal` is produced for every report run and deduplicated to the newest per ticker, so `source_path` is exactly "the most current agent report" at solve time.

Two gaps to bridge:

1. `AllocationResult` (built by the reconciler) carries **no** reference to the originating report.
2. The cluster table and the aging table need the same link helper.

---

## 3. Change 1 — `pm/models.py`

Add an optional field to `AllocationResult`:

```python
@dataclass
class AllocationResult:
    ticker: str
    # ... existing fields ...
    reason: str
    report_path: Optional[str] = None   # absolute path to the source agent report
```

---

## 4. Change 2 — `pm/engine/controller.py`

After the cash-guard block (Step 6) and **before** building `ReconciliationSummary`, attach the newest report path to each allocation. The controller already holds `parsed_signals` (post alias-merge), keyed by canonical ticker:

```python
        # Attach the newest source report path for traceability / report links.
        report_path_by_ticker = {t: s.source_path for t, s in parsed_signals.items()}
        for a in allocations:
            a.report_path = report_path_by_ticker.get(a.ticker)
```

Notes:
- The alias merge (GOOG/GOOGL) already writes the canonical ticker's signal into `parsed_signals[canonical]`, so `report_path_by_ticker` is correct for merged classes.
- Holdings with no report (defaulted to `EQUAL_WEIGHT`) simply get `None` and fall back to a plain ticker label.
- `aging_signals` / `expired_signals` are `ParsedSignal`s and already carry `source_path`, so the reporter can link them directly.

---

## 5. Change 3 — `pm/output/reporter.py`

### 5a. New imports and helper

```python
import os
import re
from pathlib import Path
from urllib.parse import quote
from typing import Optional
from pm.models import AllocationResult, ParsedSignal, ReconciliationSummary

_RUN_DIR_RE = re.compile(r"^[A-Z0-9]+_\d{8}_\d{6}$")
```

Add a method to `MarkdownTradeReporter`:

```python
    def _report_md_link(self, ticker: str, source_path: Optional[str]) -> str:
        """
        Return a table-safe Markdown link to the source agent report, or the
        bare ticker when no report is available.

        Example -> [AAPL](../01_agent_reports/AAPL/AAPL_20260905_105303/complete_report.md)
        """
        if not source_path:
            return ticker

        target = Path(source_path)

        # Walk up to the run folder (<TICKER>_<YYYYMMDD>_<HHMMSS>).
        run_dir = next(
            (p for p in (target.parent, *target.parents) if _RUN_DIR_RE.match(p.name)),
            None,
        )
        # Prefer the full report inside the run folder; else use source_path as-is.
        if run_dir is not None and (run_dir / "complete_report.md").exists():
            target = run_dir / "complete_report.md"

        # Relative to the vault output dir (e.g. 00_trade_orders) -> ../01_agent_reports/...
        rel = os.path.relpath(target, start=str(self.output_dir)).replace(os.sep, "/")
        return f"[{ticker}]({quote(rel)})"
```

### 5b. Execution Directives table

Replace the ticker cell:

```python
# before
f"| **[[{a.ticker}]]** | {curr_shares_str} | ..."
# after
f"| **{self._report_md_link(a.ticker, a.report_path)}** | {curr_shares_str} | ..."
```

### 5c. Aging & Stale Reports table

Replace the ticker cell (uses `ParsedSignal.source_path`):

```python
# before
f"| **[[{s.ticker}]]** | {s.date.isoformat()} | ..."
# after
f"| **{self._report_md_link(s.ticker, s.source_path)}** | {s.date.isoformat()} | ..."
```

### 5d. Full Portfolio Rebalance & Drift Ledger table

Same as the Directives table:

```python
f"| **{self._report_md_link(a.ticker, a.report_path)}** | {curr_shares_str} | ..."
```

### 5e. Correlated Asset Clusters table

Look each member up in `alloc_by_ticker` (already built just above) and link it:

```python
        for c_id, members in sorted(summary.clusters.items()):
            c_weight = sum(alloc_by_ticker[m].target_weight for m in members if m in alloc_by_ticker)
            assets_str = ", ".join(
                self._report_md_link(m, alloc_by_ticker[m].report_path if m in alloc_by_ticker else None)
                for m in sorted(members)
            )
            status = "✅ OK" if c_weight <= 25.01 else "⚠️ CAPPED"
            lines.append(f"| {c_id} | {assets_str} | {c_weight:.2f}% | 25.00% | {status} |")
```

---

## 6. Edge cases & decisions

| Case | Behavior |
| --- | --- |
| Equivalent classes (GOOG/GOOGL) | Canonical ticker's `source_path` is used; the alias already merged in the controller. |
| Holding with no report | `report_path` is `None` → bare (unlinked) ticker label. |
| Signal from `00_Portfolio_Actions_Dashboard.md` fallback | `source_path` is the dashboard file, not a run dir; helper links to the dashboard (still valid) — or return `ticker` if you prefer no link there. |
| Parser picked `5_portfolio/decision.md` | Helper upgrades the target to `complete_report.md` when it exists, so links are consistent. |
| Paths with spaces (future tickers) | `quote(rel)` percent-encodes them; `..` and `/` are preserved. |
| Cross-platform separators | `.replace(os.sep, "/")` keeps links portable. |

---

## 7. Optional configuration (`config.yaml`)

To make the behavior toggleable without code edits:

```yaml
output:
  report_link_style: "markdown"    # "markdown" | "wikilink" | "none"
  prefer_complete_report: true     # link complete_report.md over decision.md
```

Wire these into `MarkdownTradeReporter.__init__` and branch inside `_report_md_link`. Default behavior should remain `"markdown"` so no config change is required.

---

## 8. Verification

1. **Unit test** (`tests/test_reporter_links.py`): construct a `ReconciliationSummary` with two allocations (one with `report_path`, one without), call `generate_report_markdown`, and assert:
   - the linked row contains `[AAPL](../01_agent_reports/AAPL/AAPL_20260905_105303/complete_report.md)`;
   - the report-less row renders a bare ticker;
   - **no `[[` appears anywhere** in the output.
2. **Command check:** `python main.py --preview` then `python main.py --execute`; grep the generated note for `[[` (expect none) and confirm every `](../...md)` target exists on disk.
3. **Regression:** run `pytest tests -v`.

---

## 9. Alternative (not recommended)

Keep wikilinks but escape the pipe: `[[01_agent_reports/<T>/<RUN>/complete_report\|<T>]]`. This renders correctly in Obsidian but is **fragile** — any table auto-formatter that unescapes `\|` re-breaks the table, which is exactly what happened to `Trade_Orders_2026-09-10.md`. Markdown links avoid the failure mode entirely.

---

## 10. Documentation follow-up

Update `docs/SYSTEM_SPEC.md` §5 (Output Directives) — the example rows currently show `**[[META]]**` / `**[[ADBE]]**`; change them to the Markdown-link form and note that the cluster table members are linked the same way.
