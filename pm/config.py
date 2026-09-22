from pathlib import Path
from typing import Any, Dict, List


class ConfigurationError(ValueError):
    """Raised when configuration would make a solver run unsafe or ambiguous."""


def validate_config(config: Dict[str, Any], config_path: str) -> List[str]:
    """Validate configuration values and return resolved path warnings."""
    if not isinstance(config, dict):
        raise ConfigurationError("Configuration root must be a mapping.")

    risk = config.get("risk", {})
    rebalance = config.get("rebalance", {})
    stepping = rebalance.get("stepping", {})
    paths = config.get("paths", {})
    warnings: List[str] = []

    bounded = (
        ("risk.max_position_weight", risk.get("max_position_weight", 0.15), 0, 1),
        ("risk.min_cash_reserve", risk.get("min_cash_reserve", 0.15), 0, 1),
        ("risk.max_cluster_exposure", risk.get("max_cluster_exposure", 0.25), 0, 1),
    )
    for name, value, lower, upper in bounded:
        if not isinstance(value, (int, float)) or not lower < value <= upper:
            raise ConfigurationError(f"{name} must be greater than {lower} and at most {upper}.")

    positive = (
        ("rebalance.min_dollar_trade", rebalance.get("min_dollar_trade", 1500.0)),
        ("rebalance.stepping.max_trade_dollar_cap", stepping.get("max_trade_dollar_cap", 6000.0)),
        ("signals.max_age_days", config.get("signals", {}).get("max_age_days", 14)),
    )
    for name, value in positive:
        if not isinstance(value, (int, float)) or value < 0:
            raise ConfigurationError(f"{name} must be non-negative.")

    config_dir = Path(config_path).resolve().parent
    for key in ("reports_dir", "downloads_dir", "obsidian_vault_dir"):
        value = paths.get(key)
        if value:
            resolved = Path(value) if Path(value).is_absolute() else config_dir / value
            if key != "obsidian_vault_dir" and not resolved.exists():
                warnings.append(f"Configured {key} does not exist yet: {resolved}")

    watchlist = config.get("watchlist", {})
    signals = config.get("signals", {})
    if signals.get("expired_holding_policy", "neutral") not in {"neutral", "cap", "trim"}:
        raise ConfigurationError("signals.expired_holding_policy must be neutral, cap, or trim.")
    if signals.get("long_expired_policy", "trim") not in {"neutral", "cap", "trim"}:
        raise ConfigurationError("signals.long_expired_policy must be neutral, cap, or trim.")
    if signals.get("missing_report_buy_policy", "block") not in {"block", "allow"}:
        raise ConfigurationError("signals.missing_report_buy_policy must be block or allow.")
    stocks_file = watchlist.get("stocks_file")
    if stocks_file:
        resolved = Path(stocks_file) if Path(stocks_file).is_absolute() else config_dir / stocks_file
        if not resolved.exists():
            raise ConfigurationError(f"Configured watchlist file does not exist: {resolved}")

    return warnings
