from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from pm.agents.bridge import AgentEvaluationResult, TradingAgentsBridge
from pm.models import TriageItem


@pytest.fixture
def bridge_config(tmp_path):
    reports = tmp_path / "vault" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    return {
        "paths": {
            "reports_dir": str(reports),
            "downloads_dir": str(tmp_path / "downloads"),
        },
        "watchlist": {
            "rate_limit_delay_seconds": 0.0,
        },
        "tradingagents": {
            "llm_provider": "anthropic",
            "quick_think_llm": "claude-3-5-haiku-20241022",
            "deep_think_llm": "claude-3-5-sonnet-20241022",
            "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 1,
        },
    }


def test_build_agent_config(bridge_config):
    bridge = TradingAgentsBridge(bridge_config)
    agent_cfg = bridge._build_agent_config()

    assert agent_cfg["results_dir"] == bridge_config["paths"]["reports_dir"]
    assert agent_cfg["llm_provider"] == "anthropic"
    assert agent_cfg["quick_think_llm"] == "claude-3-5-haiku-20241022"


def test_build_agent_config_google_gemini(tmp_path):
    reports = tmp_path / "vault" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    cfg = {
        "paths": {"reports_dir": str(reports)},
        "tradingagents": {
            "llm_provider": "google",
            "quick_think_llm": "gemini-3.1-flash-lite",
            "deep_think_llm": "gemini-3.5-flash",
            "google_thinking_level": "high",
            "max_debate_rounds": 5,
            "max_risk_discuss_rounds": 5,
        },
    }
    bridge = TradingAgentsBridge(cfg)
    agent_cfg = bridge._build_agent_config()

    assert agent_cfg["llm_provider"] == "google"
    assert agent_cfg["quick_think_llm"] == "gemini-3.1-flash-lite"
    assert agent_cfg["deep_think_llm"] == "gemini-3.5-flash"
    assert agent_cfg["google_thinking_level"] == "high"
    assert agent_cfg["max_debate_rounds"] == 5
    assert agent_cfg["max_risk_discuss_rounds"] == 5



def test_evaluate_ticker_success(bridge_config):
    bridge = TradingAgentsBridge(bridge_config)

    mock_graph_cls = MagicMock()
    mock_graph_instance = MagicMock()
    mock_graph_cls.return_value = mock_graph_instance

    mock_state = {"mock_key": "val"}
    mock_signal = MagicMock()
    mock_signal.value = "Overweight"
    mock_graph_instance.propagate.return_value = (mock_state, mock_signal)
    mock_graph_instance.save_reports.return_value = Path("/mock/vault/reports/reports/AAPL_20260907_120000")

    with patch.dict("sys.modules", {"tradingagents.graph.trading_graph": MagicMock(TradingAgentsGraph=mock_graph_cls)}):
        result = bridge.evaluate_ticker(
            ticker="AAPL",
            trade_date="2026-09-07",
            asset_type="stock",
            analysts=["market", "news"],
        )

    assert result.success is True
    assert result.ticker == "AAPL"
    assert result.signal == "Overweight"
    assert "/mock/vault/reports/reports/AAPL_20260907_120000" in result.report_path
    mock_graph_instance.propagate.assert_called_once_with(
        company_name="AAPL",
        trade_date="2026-09-07",
        asset_type="stock",
    )
    assert mock_graph_instance.save_reports.call_count == 1
    call_args, call_kwargs = mock_graph_instance.save_reports.call_args
    assert call_args == (mock_state, "AAPL")
    save_path = call_kwargs.get("save_path")
    assert save_path is not None
    assert str(save_path).startswith(str(Path(bridge_config["paths"]["reports_dir"]) / "AAPL"))


def test_evaluate_ticker_failure_isolation(bridge_config):
    bridge = TradingAgentsBridge(bridge_config)

    mock_graph_cls = MagicMock()
    mock_graph_instance = MagicMock()
    mock_graph_cls.return_value = mock_graph_instance
    mock_graph_instance.propagate.side_effect = RuntimeError("OpenAI API rate limit exceeded (429)")

    with patch.dict("sys.modules", {"tradingagents.graph.trading_graph": MagicMock(TradingAgentsGraph=mock_graph_cls)}):
        result = bridge.evaluate_ticker(ticker="FAIL")

    assert result.success is False
    assert result.ticker == "FAIL"
    assert result.signal is None
    assert "rate limit exceeded" in result.error_message


def test_run_batch(bridge_config):
    bridge = TradingAgentsBridge(bridge_config)

    items = [
        TriageItem(
            ticker="AAPL", category="equities", asset_type="stock", analysts=["market"],
            in_portfolio=True, status="MISSING", priority=1
        ),
        TriageItem(
            ticker="NVDA", category="equities", asset_type="stock", analysts=["market"],
            in_portfolio=False, status="MISSING", priority=2
        ),
        TriageItem(
            ticker="MSFT", category="equities", asset_type="stock", analysts=["market"],
            in_portfolio=False, status="MISSING", priority=2
        ),
    ]

    mock_res_aapl = AgentEvaluationResult(ticker="AAPL", signal="Overweight", report_path="/path/aapl", success=True)
    mock_res_nvda = AgentEvaluationResult(ticker="NVDA", signal="Buy", report_path="/path/nvda", success=True)

    with patch.object(bridge, "evaluate_ticker", side_effect=[mock_res_aapl, mock_res_nvda]) as mock_eval:
        results = bridge.run_batch(items, limit=2)

    assert len(results) == 2
    assert results[0].ticker == "AAPL"
    assert results[1].ticker == "NVDA"
    assert mock_eval.call_count == 2
