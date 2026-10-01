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


def test_validate_config_risk_clustering_parameters(tmp_path: Path):
    valid_config = {
        "risk": {
            "max_single_stock_exposure": 0.15,
            "max_cluster_exposure": 0.25,
            "max_assets_per_cluster": 4,
            "clustering": {
                "k": 10,
                "criterion": "maxclust",
                "method": "average",
                "concentration_guide": "composite",
                "distance_threshold": 0.70,
            },
        }
    }
    warnings = validate_config(valid_config, str(tmp_path / "config.yaml"))
    assert warnings == []

    # Invalid k
    invalid_k = {"risk": {"clustering": {"k": 1}}}
    with pytest.raises(ConfigurationError, match="risk.clustering.k must be an integer of at least 2"):
        validate_config(invalid_k, str(tmp_path / "config.yaml"))

    # Invalid max_assets_per_cluster
    invalid_assets = {"risk": {"max_assets_per_cluster": 0}}
    with pytest.raises(ConfigurationError, match="risk.max_assets_per_cluster must be an integer of at least 1"):
        validate_config(invalid_assets, str(tmp_path / "config.yaml"))

    # Invalid concentration_guide
    invalid_guide = {"risk": {"clustering": {"concentration_guide": "random"}}}
    with pytest.raises(ConfigurationError, match="risk.clustering.concentration_guide"):
        validate_config(invalid_guide, str(tmp_path / "config.yaml"))

