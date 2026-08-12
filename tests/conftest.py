import pathlib

import pytest

from fpr import config
from fpr.adapters import raw_csv
from fpr.core.consensus import build

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CSV_PATH = REPO_ROOT / "raw_rankings.csv"
CONFIG_PATH = REPO_ROOT / "config" / "league.yaml"


@pytest.fixture(scope="session")
def config_path():
    return CONFIG_PATH


@pytest.fixture(scope="session")
def cfg():
    return config.load(CONFIG_PATH)


@pytest.fixture(scope="session")
def raw_players():
    return raw_csv.load(CSV_PATH)


@pytest.fixture(scope="session")
def real_table(cfg, raw_players):
    """Consensus built from the real CSV, shared across the suite.

    Named distinctly because several test modules define their own small
    hand-built table as `table`.
    """
    return build(raw_players, cfg)
