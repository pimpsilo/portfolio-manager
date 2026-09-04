import logging
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Known baseline market caps (billions) as fallback if yfinance fast_info fails
DEFAULT_MARKET_CAPS = {
    "AAPL": 3450e9, "MSFT": 3300e9, "NVDA": 3100e9, "AMZN": 2100e9,
    "GOOG": 2050e9, "GOOGL": 2050e9, "META": 1550e9, "MA": 480e9,
    "COST": 410e9, "ASML": 360e9, "NFLX": 310e9, "AMD": 245e9,
    "CRM": 235e9, "ADBE": 220e9, "INTU": 185e9, "BKNG": 165e9,
    "SPGI": 155e9, "UBER": 150e9, "PDD": 130e9, "MELI": 95e9,
    "MCO": 92e9, "CPRT": 52e9, "TQQQ": 50e9, "APP": 48e9,
    "HIG": 42e9, "KEYS": 28e9, "DECK": 25e9, "WDC": 24e9,
    "LULU": 22e9, "EG": 18e9, "ALAB": 16e9, "RDDT": 15e9,
    "DUOL": 12e9, "IMAX": 2e9, "FOX": 18e9, "ROL": 20e9,
    "CIEN": 15e9, "MU": 120e9, "TER": 25e9, "GEV": 65e9,
    "HLIT": 1e9, "UHS": 14e9, "TXRH": 12e9, "NBIS": 4e9,
    "NKE": 110e9, "CAVA": 14e9, "CINF": 22e9,
}


class MarketDataService:
    """
    Fetches real-time prices, market caps, and historical price series via yfinance.
    """

    def __init__(self, lookback_days: int = 180):
        self.lookback_days = lookback_days
        self._price_cache: Dict[str, float] = {}
        self._market_cap_cache: Dict[str, float] = {}
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

    def fetch_market_caps(self, tickers: List[str]) -> Dict[str, float]:
        """
        Retrieves market capitalization for tickers via yfinance, with institutional fallbacks.
        """
        if not tickers:
            return {}

        results: Dict[str, float] = {}
        unique_tickers = list(sorted(set(tickers)))

        try:
            yf_tickers = yf.Tickers(" ".join(unique_tickers))
            for sym in unique_tickers:
                mc = None
                try:
                    ticker_obj = yf_tickers.tickers.get(sym)
                    if ticker_obj:
                        fast_info = getattr(ticker_obj, "fast_info", None)
                        if fast_info:
                            mc = getattr(fast_info, "market_cap", None)
                except Exception:
                    pass

                if mc and not np.isnan(mc) and mc > 0:
                    results[sym] = float(mc)
                else:
                    results[sym] = DEFAULT_MARKET_CAPS.get(sym, 35e9)
        except Exception as e:
            logger.warning(f"Error in bulk market cap fetch: {e}")
            for sym in unique_tickers:
                results[sym] = DEFAULT_MARKET_CAPS.get(sym, 35e9)

        self._market_cap_cache.update(results)
        return results

    def fetch_historical_returns(self, tickers: List[str], period: str = "6mo") -> pd.DataFrame:
        """
        Downloads historical adjusted close prices and calculates percentage daily returns.
        """
        if not tickers:
            return pd.DataFrame()

        unique_tickers = list(sorted(set(tickers)))
        logger.info(f"Fetching {period} historical data for {len(unique_tickers)} tickers...")

        try:
            data = yf.download(
                tickers=unique_tickers,
                period=period,
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )

            if data.empty:
                logger.warning("Empty price dataframe returned from yfinance")
                return pd.DataFrame()

            # Handle multi-ticker or single ticker columns
            if isinstance(data.columns, pd.MultiIndex):
                if "Close" in data.columns.levels[0]:
                    prices = data["Close"]
                else:
                    prices = data.iloc[:, :len(unique_tickers)]
            else:
                prices = data[["Close"]] if "Close" in data else data

            returns = prices.pct_change().dropna(how="all")
            self._history_cache = returns
            return returns

        except Exception as e:
            logger.error(f"Error downloading historical returns: {e}")
            return pd.DataFrame()
