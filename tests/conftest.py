import pathlib

import pytest

from fpr import config, pipeline
from fpr.adapters import raw_csv, rosters
from fpr.core import lineup
from fpr.core.consensus import build

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CSV_PATH = REPO_ROOT / "raw_rankings.csv"
CONFIG_PATH = REPO_ROOT / "config" / "league.yaml"
ROSTERS_PATH = REPO_ROOT / "config" / "rosters.example.yaml"


@pytest.fixture(scope="session")
def config_path():
    return CONFIG_PATH


@pytest.fixture(scope="session")
def csv_path():
    return CSV_PATH


@pytest.fixture(scope="session")
def rosters_path():
    return ROSTERS_PATH


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


@pytest.fixture(scope="session")
def example_rosters():
    return rosters.load(ROSTERS_PATH)


@pytest.fixture(scope="session")
def league_lineups(example_rosters, real_table, cfg):
    """Twelve real lineups off the example rosters."""
    return {
        team: lineup.build(team, roster, real_table, cfg)
        for team, roster in example_rosters.items()
    }


@pytest.fixture(scope="session")
def league():
    return pipeline.build(
        config_path=CONFIG_PATH, rankings_path=CSV_PATH, rosters_path=ROSTERS_PATH
    )


@pytest.fixture(scope="session")
def optimal_league():
    return pipeline.build(
        config_path=CONFIG_PATH,
        rankings_path=CSV_PATH,
        rosters_path=ROSTERS_PATH,
        optimal=True,
    )
