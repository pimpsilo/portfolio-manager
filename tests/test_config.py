from pathlib import Path

import pytest

from pm.config import ConfigurationError, validate_config


def test_validate_config_rejects_missing_watchlist(tmp_path: Path):
    config = {"watchlist": {"stocks_file": "missing.csv"}}

    with pytest.raises(ConfigurationError, match="watchlist file does not exist"):
        validate_config(config, str(tmp_path / "config.yaml"))


def test_validate_config_returns_missing_runtime_path_warning(tmp_path: Path):
    config = {"paths": {"reports_dir": "reports", "downloads_dir": "downloads"}}

    warnings = validate_config(config, str(tmp_path / "config.yaml"))

    assert any("reports_dir" in warning for warning in warnings)
    assert any("downloads_dir" in warning for warning in warnings)
