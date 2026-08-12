"""Config loading tests.

The point of config.py is that a bad league.yaml fails on load with a message
naming the key, instead of turning into a TypeError somewhere inside the
simulation an hour later. These tests are mostly about that.
"""

import copy

import pytest
import yaml

from fpr import config
from fpr.config import ConfigError


@pytest.fixture
def raw(config_path):
    with config_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def write(tmp_path, data):
    path = tmp_path / "league.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def load_with(tmp_path, raw, mutate):
    data = copy.deepcopy(raw)
    mutate(data)
    return config.load(write(tmp_path, data))


class TestTheRealConfig:
    def test_it_loads(self, cfg):
        assert cfg.teams == 12
        assert len(cfg.slots) == 10

    def test_starter_and_bench_split(self, cfg):
        assert cfg.starter_slots == ("QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX1", "FLEX2")
        assert cfg.bench_slots == ("BN1", "BN2")

    def test_starters_all_weigh_one(self, cfg):
        assert {cfg.slot_weights[s] for s in cfg.starter_slots} == {1.0}

    def test_bench_weighs_less_than_a_starter(self, cfg):
        assert 0 < cfg.configured_bench_weight < 1.0

    def test_qb_is_not_bench_eligible(self, cfg):
        # Deliberate: a backup QB in a single-QB league is close to worthless.
        assert "QB" not in cfg.bench_eligible_positions
        assert set(cfg.bench_eligible_positions) == {"RB", "WR", "TE"}


class TestAvailability:
    def test_running_backs_are_the_most_fragile(self, cfg):
        rates = cfg.availability.position_base_rate
        assert rates["RB"] == min(rates.values())
        assert rates["QB"] == max(rates.values())

    def test_healthy_player_gets_the_base_rate(self, cfg):
        assert cfg.availability.rate_for("RB", None) == pytest.approx(0.79)
        assert cfg.availability.rate_for("RB", "ACTIVE") == pytest.approx(0.79)

    def test_designation_knocks_the_rate_down(self, cfg):
        healthy = cfg.availability.rate_for("WR", "ACTIVE")
        questionable = cfg.availability.rate_for("WR", "QUESTIONABLE")
        doubtful = cfg.availability.rate_for("WR", "DOUBTFUL")
        assert healthy > questionable > doubtful

    def test_injured_reserve_is_near_zero_but_not_zero(self, cfg):
        rate = cfg.availability.rate_for("RB", "INJURY_RESERVE")
        assert 0 < rate < 0.1

    def test_clamped_at_both_ends(self, cfg):
        low, high = cfg.availability.clamp
        for pos in ("QB", "RB", "WR", "TE"):
            for status in cfg.availability.status_multiplier:
                assert low <= cfg.availability.rate_for(pos, status) <= high

    def test_status_lookup_is_case_insensitive(self, cfg):
        assert cfg.availability.rate_for("rb", "questionable") == cfg.availability.rate_for(
            "RB", "QUESTIONABLE"
        )

    def test_unknown_status_is_treated_as_healthy(self, cfg):
        # Platforms invent designations. An unrecognized one shouldn't silently
        # zero a player out.
        assert cfg.availability.rate_for("RB", "SOMETHING_NEW") == pytest.approx(0.79)

    def test_unknown_position_raises(self, cfg):
        with pytest.raises(ConfigError, match="no availability base rate"):
            cfg.availability.rate_for("K", "ACTIVE")


class TestValidation:
    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="config file not found"):
            config.load(tmp_path / "nope.yaml")

    def test_missing_key_names_the_key(self, tmp_path, raw):
        with pytest.raises(ConfigError, match="'slot_weights'"):
            load_with(tmp_path, raw, lambda d: d.pop("slot_weights"))

    def test_slot_without_a_weight(self, tmp_path, raw):
        with pytest.raises(ConfigError, match="slots with no configured weight"):
            load_with(tmp_path, raw, lambda d: d["slot_weights"].pop("FLEX2"))

    def test_weight_for_an_unknown_slot(self, tmp_path, raw):
        with pytest.raises(ConfigError, match="unknown slots"):
            load_with(tmp_path, raw, lambda d: d["slot_weights"].update({"BN9": 0.1}))

    def test_inverted_value_curve(self, tmp_path, raw):
        with pytest.raises(ConfigError, match="floor .* must be below ceiling"):
            load_with(tmp_path, raw, lambda d: d["value_curve"].update({"floor": 400.0}))

    def test_negative_decay_inverts_the_curve(self, tmp_path, raw):
        with pytest.raises(ConfigError, match="decay must be positive"):
            load_with(tmp_path, raw, lambda d: d["value_curve"].update({"decay": -1.0}))

    def test_zero_trials(self, tmp_path, raw):
        with pytest.raises(ConfigError, match="trials must be positive"):
            load_with(tmp_path, raw, lambda d: d["simulation"].update({"trials": 0}))

    def test_adding_qb_to_bench_eligibility_is_rejected(self, tmp_path, raw):
        with pytest.raises(ConfigError, match="rewards hoarding backup QBs"):
            load_with(
                tmp_path, raw, lambda d: d.update({"bench_eligible_positions": ["QB", "RB", "WR"]})
            )

    def test_roster_shape_must_match_the_slot_list(self, tmp_path, raw):
        with pytest.raises(ConfigError, match="describes 9 starters but slots list 8"):
            load_with(tmp_path, raw, lambda d: d["roster_shape"].update({"rb": 3}))

    def test_bench_min_beyond_scored_bench_slots(self, tmp_path, raw):
        with pytest.raises(ConfigError, match="exceeds the 2 bench slots"):
            load_with(tmp_path, raw, lambda d: d["roster_shape"].update({"bench_min": 5}))

    def test_bad_clamp(self, tmp_path, raw):
        with pytest.raises(ConfigError, match="clamp must be two values"):
            load_with(tmp_path, raw, lambda d: d["availability"].update({"clamp": [0.9, 0.1]}))

    def test_missing_position_base_rate(self, tmp_path, raw):
        with pytest.raises(ConfigError, match="missing TE"):
            load_with(tmp_path, raw, lambda d: d["availability"]["position_base_rate"].pop("TE"))

    def test_mismatched_bench_weights_are_ambiguous(self, tmp_path, raw):
        cfg = load_with(tmp_path, raw, lambda d: d["slot_weights"].update({"BN2": 0.5}))
        # Loads fine, but the simulation can't compare one measured weight
        # against two configured ones.
        with pytest.raises(ConfigError, match="differing weights"):
            _ = cfg.configured_bench_weight

    def test_not_a_mapping(self, tmp_path):
        path = tmp_path / "league.yaml"
        path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="did not parse to a mapping"):
            config.load(path)
