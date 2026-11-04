import copy
from dataclasses import dataclass
from datetime import datetime
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import yaml

from pm.models import TriageItem

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True))
    # Check sibling TradingAgents .env fallback
    _sibling_env = Path(__file__).resolve().parents[3] / "TradingAgents" / ".env"
    if _sibling_env.exists():
        load_dotenv(_sibling_env, override=False)
except ImportError:
    pass

# Harmonize API keys: Google SDK emits a warning if both GOOGLE_API_KEY and GEMINI_API_KEY are present
if "GOOGLE_API_KEY" in os.environ and "GEMINI_API_KEY" in os.environ:
    if os.environ["GOOGLE_API_KEY"]:
        os.environ.pop("GEMINI_API_KEY", None)

# Expand google.genai transient exceptions to cover server disconnects and protocol errors
try:
    import httpx
    try:
        import httpcore
        _httpcore_errs = (
            httpcore.RemoteProtocolError,
            httpcore.ReadError,
            httpcore.WriteError,
            httpcore.NetworkError,
            httpcore.TimeoutException,
        )
    except ImportError:
        _httpcore_errs = ()
    from google.genai import _api_client
    _extra_transient = (
        httpx.RemoteProtocolError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.TransportError,
        httpx.NetworkError,
        ConnectionResetError,
        BrokenPipeError,
    ) + _httpcore_errs
    _api_client._HTTPX_TRANSIENT_EXC = tuple(set(_api_client._HTTPX_TRANSIENT_EXC + _extra_transient))
except Exception:
    pass

# Suppress known noisy SDK warnings
try:
    from google.genai.models import Models
    Models._logged_afc_warning = True
except Exception:
    pass


def _patch_yfinance_get_news() -> None:
    """
    Workaround for Yahoo Finance retiring `/xhr/ncp` (HTTP 404), which causes
    `yfinance.Ticker.get_news()` to raise `YFDataException('*** YAHOO! FINANCE IS CURRENTLY DOWN! ***')`.
    Replaces `TickerBase.get_news` with Yahoo Finance's active ticker RSS feed
    (`feeds.finance.yahoo.com/rss/2.0/headline`) enriched with `yf.Search` metadata.
    """
    try:
        from email.utils import parsedate_to_datetime
        import xml.etree.ElementTree as ET
        import yfinance as yf
        from yfinance.base import TickerBase

        if getattr(TickerBase.get_news, "_pm_patched", False):
            return

        _orig_get_news = TickerBase.get_news

        def _get_news_patched(self, count: int = 10, tab: str = "news") -> list:
            if getattr(self, "_news", None):
                return self._news

            symbol = str(getattr(self, "ticker", "") or "").strip()
            if not symbol:
                return []

            articles: list = []
            seen_keys: set = set()
            publisher_by_uuid: dict = {}
            publisher_by_title: dict = {}
            search_articles: list = []

            # 1. Fetch from Yahoo Finance v1/finance/search for publisher metadata & fallback
            try:
                search_res = yf.Search(
                    query=symbol,
                    news_count=max(int(count or 10), 20),
                    enable_fuzzy_query=False,
                    raise_errors=False,
                )
                search_articles = list(getattr(search_res, "news", None) or [])
                for item in search_articles:
                    uuid_val = item.get("uuid")
                    title_val = (item.get("title") or "").strip()
                    pub_val = item.get("publisher")
                    if pub_val:
                        if uuid_val:
                            publisher_by_uuid[uuid_val] = pub_val
                        if title_val:
                            publisher_by_title[title_val.lower()] = pub_val
            except Exception:
                pass

            # 2. Fetch ticker-specific articles + summaries from Yahoo Finance's RSS headline feed
            try:
                rss_url = "https://feeds.finance.yahoo.com/rss/2.0/headline"
                resp = self._data.cache_get(
                    url=rss_url,
                    params={"s": symbol, "region": "US", "lang": "en-US"},
                    timeout=15,
                )
                if resp is not None and getattr(resp, "status_code", 0) == 200 and resp.text:
                    root = ET.fromstring(resp.text)
                    for item in root.findall(".//item"):
                        title = (item.findtext("title") or "").strip()
                        if not title:
                            continue
                        guid = (item.findtext("guid") or "").strip()
                        link = (item.findtext("link") or "").strip()
                        if link.endswith("?.tsrc=rss"):
                            link = link[: -len("?.tsrc=rss")]
                        desc = (item.findtext("description") or "").strip()
                        pub_str = (item.findtext("pubDate") or "").strip()

                        pub_ts = None
                        if pub_str:
                            try:
                                dt = parsedate_to_datetime(pub_str)
                                pub_ts = int(dt.timestamp())
                            except Exception:
                                pub_ts = None

                        publisher = (
                            publisher_by_uuid.get(guid)
                            or publisher_by_title.get(title.lower())
                            or "Yahoo Finance"
                        )
                        dedup_key = guid or title.lower()
                        if dedup_key in seen_keys:
                            continue
                        seen_keys.add(dedup_key)
                        seen_keys.add(title.lower())

                        entry = {
                            "uuid": guid,
                            "title": title,
                            "summary": desc,
                            "publisher": publisher,
                            "link": link,
                            "type": "STORY",
                            "relatedTickers": [symbol.upper()],
                        }
                        if pub_ts is not None:
                            entry["providerPublishTime"] = pub_ts
                        articles.append(entry)
            except Exception:
                pass

            # 3. Supplement with symbol-matched Search articles not already in RSS
            sym_upper = symbol.upper()
            for item in search_articles:
                related = [str(t).upper() for t in (item.get("relatedTickers") or [])]
                if sym_upper not in related:
                    continue
                uuid_val = item.get("uuid") or ""
                title_val = (item.get("title") or "").strip()
                if (uuid_val and uuid_val in seen_keys) or (title_val and title_val.lower() in seen_keys):
                    continue
                if uuid_val:
                    seen_keys.add(uuid_val)
                if title_val:
                    seen_keys.add(title_val.lower())
                articles.append(item)

            if articles:
                limit = int(count) if count and count > 0 else len(articles)
                self._news = articles[:limit]
                return self._news

            # 4. Last-resort fallback to original yfinance implementation
            return _orig_get_news(self, count=count, tab=tab)

        _get_news_patched._pm_patched = True
        TickerBase.get_news = _get_news_patched
    except Exception:
        pass


_patch_yfinance_get_news()

import warnings
warnings.filterwarnings("ignore", message=".*is not a valid FinishReason.*")

logger = logging.getLogger(__name__)


@dataclass
class AgentEvaluationResult:
    ticker: str
    signal: Optional[str]
    report_path: Optional[str]
    success: bool
    error_message: Optional[str] = None
    duration_seconds: float = 0.0


class TradingAgentsBridge:
    """
    Bridge interfacing portfolio-manager with the TradingAgents multi-agent
    framework to execute research evaluations and save Obsidian-compatible reports.
    """

    def __init__(self, config_or_path: Any = "config.yaml", debug: bool = False):
        if isinstance(config_or_path, dict):
            self.config = config_or_path
        else:
            with open(config_or_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f)

        paths = self.config.get("paths", {})
        self.reports_dir = Path(paths.get("reports_dir", "/Users/matthewhope/Library/Mobile Documents/iCloud~md~obsidian/Documents/Portfolio/01_agent_reports"))
        self.debug = debug

        self.tradingagents_cfg = self.config.get("tradingagents", {})
        self.watchlist_cfg = self.config.get("watchlist", {})
        self.rate_limit_delay = float(self.watchlist_cfg.get("rate_limit_delay_seconds", 2.0))

    def _build_agent_config(self) -> Dict[str, Any]:
        """Merges portfolio-manager config into TradingAgents DEFAULT_CONFIG."""
        try:
            from tradingagents.default_config import DEFAULT_CONFIG
            base_config = copy.deepcopy(DEFAULT_CONFIG)
        except ImportError:
            base_config = {}

        # Ensure reports are written inside reports_dir so MarkdownSignalParser finds them
        reports_target = str(self.reports_dir)
        base_config["results_dir"] = reports_target
        try:
            os.makedirs(reports_target, exist_ok=True)
        except OSError:
            pass

        # Apply tradingagents section overrides from config.yaml
        for key, value in self.tradingagents_cfg.items():
            if value is not None:
                base_config[key] = value

        return base_config

    @staticmethod
    def get_latest_settled_trade_date(reference_symbol: str = "SPY") -> str:
        """
        Determines the latest trading date that has fully settled closing prices.
        If today's market data is unsettled (e.g. after-hours with NaN close in Yahoo Finance),
        returns the preceding settled trading day.
        """
        try:
            import yfinance as yf
            df = yf.download(reference_symbol, period="5d", progress=False, auto_adjust=True)
            if not df.empty and "Close" in df.columns:
                closes = df["Close"].dropna()
                if not closes.empty:
                    settled = closes.index[-1].strftime("%Y-%m-%d")
                    return settled
        except Exception as e:
            logger.debug(f"Could not verify settled trade date via {reference_symbol}: {e}")
        return datetime.now().strftime("%Y-%m-%d")

    def evaluate_ticker(
        self,
        ticker: str,
        trade_date: Optional[str] = None,
        asset_type: str = "stock",
        analysts: Optional[List[str]] = None,
        max_attempts: int = 2,
    ) -> AgentEvaluationResult:
        """
        Runs the TradingAgentsGraph pipeline for a single ticker and writes
        the report tree directly to the Obsidian vault reports directory.
        Includes automatic retry for transient network and protocol errors.
        """
        start_time = time.time()
        if trade_date is None:
            trade_date = self.get_latest_settled_trade_date()

        if analysts is None:
            analysts = ["market", "news", "fundamentals"]

        ticker_clean = ticker.strip().upper()
        logger.info(f"🚀 Starting TradingAgents research for {ticker_clean} (trade_date={trade_date}, asset_type={asset_type}, analysts={analysts})...")

        for attempt in range(1, max_attempts + 1):
            try:
                from tradingagents.graph.trading_graph import TradingAgentsGraph

                agent_config = self._build_agent_config()
                graph = TradingAgentsGraph(
                    selected_analysts=analysts,
                    debug=self.debug,
                    config=agent_config,
                )

                final_state, signal = graph.propagate(
                    company_name=ticker_clean,
                    trade_date=trade_date,
                    asset_type=asset_type,
                )

                # Save the full report tree into reports_dir/<TICKER>/<TICKER>_<YYYYMMDD>_<HHMMSS>/
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                target_save_path = Path(self.reports_dir) / ticker_clean / f"{ticker_clean}_{stamp}"
                report_path = graph.save_reports(final_state, ticker_clean, save_path=target_save_path)
                duration = time.time() - start_time

                signal_val = str(signal.value) if hasattr(signal, "value") else str(signal)
                logger.info(f"✅ Finished research for {ticker_clean}: Signal={signal_val} in {duration:.1f}s")

                return AgentEvaluationResult(
                    ticker=ticker_clean,
                    signal=signal_val,
                    report_path=str(report_path),
                    success=True,
                    duration_seconds=duration,
                )

            except Exception as e:
                err_str = str(e)
                duration = time.time() - start_time
                is_transient = any(sub in err_str.lower() for sub in (
                    "disconnected", "protocolerror", "timeout", "connection",
                    "reset", "503", "502", "504", "429", "rate limit"
                ))

                if attempt < max_attempts and is_transient:
                    logger.warning(
                        f"⚠️ Transient error evaluating {ticker_clean} (attempt {attempt}/{max_attempts}): {e}. Retrying in 10s..."
                    )
                    time.sleep(10)
                    continue

                logger.exception(f"❌ Failed to run research pipeline for {ticker_clean}: {e}")
                return AgentEvaluationResult(
                    ticker=ticker_clean,
                    signal=None,
                    report_path=None,
                    success=False,
                    error_message=err_str,
                    duration_seconds=duration,
                )

    def run_batch(
        self,
        queue: List[TriageItem],
        limit: Optional[int] = None,
        trade_date: Optional[str] = None,
    ) -> List[AgentEvaluationResult]:
        """
        Executes evaluations for a list of triaged candidate items with rate limiting
        and error recovery.
        """
        targets = queue[:limit] if limit is not None and limit > 0 else queue
        total = len(targets)
        results: List[AgentEvaluationResult] = []

        if total == 0:
            logger.info("Evaluation queue is empty. No tickers to evaluate.")
            return results

        if trade_date is None:
            trade_date = self.get_latest_settled_trade_date()

        print(f"\n=======================================================")
        print(f"🤖 TRADINGAGENTS PIPELINE DISPATCH ({total} Tickers, Date: {trade_date})")
        print(f"=======================================================")

        for idx, item in enumerate(targets, 1):
            print(f"[{idx}/{total}] Evaluating {item.ticker} ({item.category}, {item.asset_type})...")
            res = self.evaluate_ticker(
                ticker=item.ticker,
                trade_date=trade_date,
                asset_type=item.asset_type,
                analysts=item.analysts,
            )
            results.append(res)

            if res.success:
                prior_str = f" (Prior: {item.current_signal.value})" if item.current_signal else ""
                print(f"       ➡️  Result: {res.signal}{prior_str} ({res.duration_seconds:.1f}s) -> {res.report_path}")
            else:
                print(f"       ⚠️  Failed: {res.error_message}")

            # Inter-ticker pause to avoid burst rate-limits on LLM APIs
            if idx < total and self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)

        success_count = sum(1 for r in results if r.success)
        print("=======================================================")
        print(f"🏁 Batch complete: {success_count}/{total} successful.")

        changed = []
        for r, item in zip(results, targets):
            if r.success and item.current_signal and r.signal != item.current_signal.value:
                changed.append(f"   • {item.ticker}: {item.current_signal.value} ➡️  {r.signal}")
        if changed:
            print("\n🔄 Rating Changes Detected:")
            for ch in changed:
                print(ch)

        failed = [r for r in results if not r.success]
        if failed:
            failed_tickers = ",".join(r.ticker for r in failed)
            print("\n⚠️ Failed Tickers Detected:")
            for f in failed:
                print(f"   • {f.ticker}: {f.error_message}")
            print("\n💡 To re-run only the failed ticker(s), execute:")
            print(f"   python main.py --run-agents --tickers {failed_tickers}")

        print("=======================================================\n")

        return results
