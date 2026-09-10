#!/usr/bin/env python3
"""
Legacy batch execution script using pexpect keystroke emulation.
NOTE: For native programmatic execution without terminal emulation, prefer:
    python main.py --run-agents [--limit N]
"""
import os
import sys
import time
from pathlib import Path
import pexpect

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_STOCKS = BASE_DIR / "stocks.csv"
FALLBACK_STOCKS = Path("~/reports/stocks.txt").expanduser()

if LOCAL_STOCKS.exists():
    STOCKS_FILE = str(LOCAL_STOCKS)
elif FALLBACK_STOCKS.exists():
    STOCKS_FILE = str(FALLBACK_STOCKS)
else:
    print(f"Error: Stocks file not found at {LOCAL_STOCKS} or {FALLBACK_STOCKS}.")
    sys.exit(1)

with open(STOCKS_FILE, "r") as f:
    tickers = [line.strip() for line in f if line.strip() and not line.startswith("#")]

DOWN_ARROW = "\x1b[B"
ENTER = "\r"

# Set environment variables to prevent terminal capability queries
env = os.environ.copy()
env["PROMPT_TOOLKIT_NO_CPR"] = "1"
env["TERM"] = "xterm-256color"


def send_keys(child, text, delay=0.08):
    """Types characters individually to match prompt_toolkit's event loop speed."""
    for char in text:
        child.send(char)
        time.sleep(delay)


for ticker in tickers:
    print(f"\n==================================================")
    print(f" Running TradingAgents (Gemini) for: {ticker}")
    print(f"==================================================")

    child = pexpect.spawn(
        "tradingagents",
        encoding="utf-8",
        timeout=1800,
        dimensions=(50, 120),
        env=env,
    )

    # Stream live output directly to terminal
    child.logfile_read = sys.stdout

    try:
        # Step 1: Ticker Symbol
        child.expect(r"Enter ticker symbol")
        time.sleep(0.5)
        send_keys(child, ticker + ENTER)

        # Step 2: Analysis Date (Accept Default)
        child.expect(r"Analysis Date")
        time.sleep(0.3)
        send_keys(child, ENTER)

        # Step 3: Output Language (Accept Default)
        child.expect(r"Select Output Language")
        time.sleep(0.3)
        send_keys(child, ENTER)

        # Step 4: Analysts Team (Press 'a' to select all, then Enter)
        child.expect(r"Analysts Team")
        time.sleep(0.3)
        send_keys(child, "a" + ENTER)

        # Step 5: Research Depth (Down arrow twice for 'Deep', then Enter)
        child.expect(r"Research Depth")
        time.sleep(0.3)
        send_keys(child, DOWN_ARROW + DOWN_ARROW + ENTER)

        # Step 6: LLM Provider (Down arrow 1 time for 'Google', then Enter)
        child.expect(r"LLM Provider")
        time.sleep(0.3)
        send_keys(child, DOWN_ARROW + ENTER)

        # Step 7a: Quick-Thinking Engine (Down arrow 1 time for 'Gemini 3.1 Flash Lite', then Enter)
        child.expect(r"Quick-Thinking LLM Engine")
        time.sleep(0.3)
        send_keys(child, DOWN_ARROW + ENTER)

        # Step 7b: Deep-Thinking Engine (Down arrow 1 time for 'Gemini 3.5 Flash - Latest GA', then Enter)
        child.expect(r"Deep-Thinking LLM Engine")
        time.sleep(0.3)
        send_keys(child, DOWN_ARROW + ENTER)

        # Step 8: Enable Thinking Mode (Accept Default)
        child.expect(r"thinking mode")
        time.sleep(0.3)
        send_keys(child, ENTER)

        # Step 9: Save report? [Y]
        child.expect(r"Save report")
        time.sleep(0.3)
        send_keys(child, "Y" + ENTER)

        # Step 10: Save path (Accept Default)
        child.expect(r"Save path")
        time.sleep(0.3)
        send_keys(child, ENTER)

        # Step 11: Display full report on screen? [Y] -> Select 'n'
        child.expect(r"Display full report")
        time.sleep(0.3)
        send_keys(child, "n" + ENTER)

        child.expect(pexpect.EOF)
        print(f"\n\n✓ Completed analysis for {ticker}")

    except pexpect.TIMEOUT:
        print(f"\n❌ Timed out waiting for prompt on ticker {ticker}")
        child.close()
    except Exception as e:
        print(f"\n❌ An error occurred processing {ticker}: {e}")
        child.close()

    time.sleep(5)