import logging
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class MarketDataService:
    """
    Fetches real-time prices and historical price series via yfinance.
    """

    def __init__(self, lookback_days: int = 180):
        self.lookback_days = lookback_days
        self._price_cache: Dict[str, float] = {}
        self._history_cache: Optional[pd.DataFrame] = None

    def fetch_realtime_prices(
        self,
        tickers: List[str],
        fallback_prices: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """
        Retrieves real-time prices for a list of tickers.
        Falls back to provided fallback_prices if a quote fails.
        """
        if not tickers:
            return {}

        fallback_prices = fallback_prices or {}
        results: Dict[str, float] = {}

        unique_tickers = list(sorted(set(tickers)))
        logger.info(f"Fetching real-time market prices for {len(unique_tickers)} tickers via yfinance...")

        try:
            yf_tickers = yf.Tickers(" ".join(unique_tickers))
            for sym in unique_tickers:
                price = None
                try:
                    ticker_obj = yf_tickers.tickers.get(sym)
                    if ticker_obj:
                        fast_info = getattr(ticker_obj, "fast_info", None)
                        if fast_info:
                            price = getattr(fast_info, "last_price", None)
                        if price is None:
                            # Fallback to history
                            hist = ticker_obj.history(period="2d")
                            if not hist.empty and "Close" in hist:
                                price = float(hist["Close"].iloc[-1])
                except Exception as e:
                    logger.warning(f"Error fetching real-time price for {sym}: {e}")

                if price is not None and not np.isnan(price) and price > 0:
                    results[sym] = float(price)
                elif sym in fallback_prices and fallback_prices[sym] > 0:
                    logger.info(f"Using fallback CSV price for {sym}: ${fallback_prices[sym]:.2f}")
                    results[sym] = fallback_prices[sym]
                else:
                    results[sym] = 0.0

        except Exception as e:
            logger.error(f"Bulk ticker fetch error: {e}")
            for sym in unique_tickers:
                results[sym] = fallback_prices.get(sym, 0.0)

        self._price_cache.update(results)
        return results

    def fetch_historical_returns(self, tickers: List[str], period: str = "6mo") -> pd.DataFrame:
        """
        Downloads historical adjusted close prices and calculates percentage daily returns.
        """
        if not tickers:
            return pd.DataFrame()

        unique_tickers = list(sorted(set(tickers)))
        logger.info(f"Downloading historical returns for {len(unique_tickers)} assets...")

        try:
            data = yf.download(
                tickers=unique_tickers,
                period=period,
                interval="1d",
                progress=False,
                auto_adjust=True,
            )

            if data.empty:
                return pd.DataFrame()

            # Handle MultiIndex column when downloading multiple tickers
            if isinstance(data.columns, pd.MultiIndex):
                if "Close" in data.columns.levels[0]:
                    prices = data["Close"]
                else:
                    prices = data.xs("Close", axis=1, level=0, drop_level=True)
            elif "Close" in data.columns:
                prices = data[["Close"]]
                if len(unique_tickers) == 1:
                    prices.columns = [unique_tickers[0]]
            else:
                prices = data

            # Fill forward missing values, drop columns with all NaNs
            prices = prices.ffill().dropna(how="all", axis=1)
            returns = prices.pct_change().dropna(how="all")
            self._history_cache = returns
            return returns

        except Exception as e:
            logger.error(f"Error downloading historical returns: {e}")
            return pd.DataFrame()
