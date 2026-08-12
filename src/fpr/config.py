"""Typed config, parsed once at startup.

Everything tunable comes from config/league.yaml and gets validated here, so a
typo in the YAML fails on load with a message naming the key rather than
surfacing as a TypeError six modules deep in the simulation.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import yaml

DEFAULT_PATH = pathlib.Path("config/league.yaml")

# K and D/ST are never modeled, so this is the whole universe of positions.
SKILL_POSITIONS = ("QB", "RB", "WR", "TE")


class ConfigError(ValueError):
    """Raised for anything wrong with league.yaml."""


@dataclass(frozen=True)
class ValueCurve:
    ceiling: float
    decay: float
    floor: float


@dataclass(frozen=True)
class RosterShape:
    qb: int
    rb: int
    wr: int
    te: int
    flex: int
    bench_min: int


@dataclass(frozen=True)
class ConsensusConfig:
    unranked_offset: int


@dataclass(frozen=True)
class SimulationConfig:
    trials: int
    weeks: int
    min_spread: float
    single_source_spread: float


@dataclass(frozen=True)
class AvailabilityConfig:
    position_base_rate: dict[str, float]
    status_multiplier: dict[str, float]
    clamp: tuple[float, float]

    def rate_for(self, position: str, status: str | None) -> float:
        """Base rate for the position, knocked down by injury designation."""
        base = self.position_base_rate.get(position.upper())
        if base is None:
            raise ConfigError(f"no availability base rate configured for position {position!r}")
        mult = self.status_multiplier.get((status or "ACTIVE").upper(), 1.0)
        low, high = self.clamp
        return min(high, max(low, base * mult))


@dataclass(frozen=True)
class LeagueConfig:
    teams: int
    slots: tuple[str, ...]
    slot_weights: dict[str, float]
    bench_weight_tolerance: float
    roster_shape: RosterShape
    bench_eligible_positions: tuple[str, ...]
    value_curve: ValueCurve
    consensus: ConsensusConfig
    simulation: SimulationConfig
    availability: AvailabilityConfig
    skill_positions: tuple[str, ...] = field(default=SKILL_POSITIONS)

    @property
    def starter_slots(self) -> tuple[str, ...]:
        return tuple(s for s in self.slots if not s.startswith("BN"))

    @property
    def bench_slots(self) -> tuple[str, ...]:
        return tuple(s for s in self.slots if s.startswith("BN"))

    @property
    def configured_bench_weight(self) -> float:
        """The single bench weight, if the bench slots all share one.

        The simulation measures what this should be and compares. Distinct
        per-bench-slot weights would make that comparison ambiguous, so they're
        rejected here rather than silently averaged.
        """
        weights = {self.slot_weights[s] for s in self.bench_slots}
        if len(weights) != 1:
            raise ConfigError(f"bench slots have differing weights: {weights}")
        return weights.pop()


def _require(data: dict, key: str, where: str = "league.yaml"):
    if key not in data:
        raise ConfigError(f"missing required key {key!r} in {where}")
    return data[key]


def load(path: pathlib.Path | str = DEFAULT_PATH) -> LeagueConfig:
    path = pathlib.Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} did not parse to a mapping")

    slots = tuple(_require(raw, "slots"))
    weights = dict(_require(raw, "slot_weights"))

    missing = [s for s in slots if s not in weights]
    if missing:
        raise ConfigError(f"slots with no configured weight: {missing}")
    extra = [s for s in weights if s not in slots]
    if extra:
        raise ConfigError(f"slot_weights contains unknown slots: {extra}")

    clamp = tuple(_require(_require(raw, "availability"), "clamp"))
    if len(clamp) != 2 or not 0 < clamp[0] < clamp[1] < 1:
        raise ConfigError(
            f"availability.clamp must be two values with 0 < low < high < 1, got {clamp}"
        )

    cfg = LeagueConfig(
        teams=int(_require(raw, "teams")),
        slots=slots,
        slot_weights={k: float(v) for k, v in weights.items()},
        bench_weight_tolerance=float(_require(raw, "bench_weight_tolerance")),
        roster_shape=RosterShape(**_require(raw, "roster_shape")),
        bench_eligible_positions=tuple(_require(raw, "bench_eligible_positions")),
        value_curve=ValueCurve(**_require(raw, "value_curve")),
        consensus=ConsensusConfig(**_require(raw, "consensus")),
        simulation=SimulationConfig(**_require(raw, "simulation")),
        availability=AvailabilityConfig(
            position_base_rate=dict(_require(raw["availability"], "position_base_rate")),
            status_multiplier=dict(_require(raw["availability"], "status_multiplier")),
            clamp=(float(clamp[0]), float(clamp[1])),
        ),
    )

    _validate(cfg)
    return cfg


def _validate(cfg: LeagueConfig) -> None:
    if cfg.teams < 2:
        raise ConfigError(f"teams must be at least 2, got {cfg.teams}")

    curve = cfg.value_curve
    if curve.decay <= 0:
        raise ConfigError("value_curve.decay must be positive or the curve inverts")
    if curve.floor >= curve.ceiling:
        raise ConfigError(
            f"value_curve.floor ({curve.floor}) must be below ceiling ({curve.ceiling})"
        )

    sim = cfg.simulation
    if sim.trials < 1:
        raise ConfigError(f"simulation.trials must be positive, got {sim.trials}")
    if sim.weeks < 1:
        raise ConfigError(f"simulation.weeks must be positive, got {sim.weeks}")
    if sim.min_spread <= 0:
        raise ConfigError("simulation.min_spread must be positive")

    # QB on the bench is excluded on purpose (a backup QB in a single-QB league
    # is close to worthless). Catch it here if someone adds it back by accident.
    if "QB" in cfg.bench_eligible_positions:
        raise ConfigError(
            "QB is bench-eligible in config, which rewards hoarding backup QBs. "
            "Remove it from bench_eligible_positions."
        )

    unknown = set(cfg.bench_eligible_positions) - set(cfg.skill_positions)
    if unknown:
        raise ConfigError(f"bench_eligible_positions has non-skill positions: {sorted(unknown)}")

    for pos in cfg.skill_positions:
        if pos not in cfg.availability.position_base_rate:
            raise ConfigError(f"availability.position_base_rate is missing {pos}")

    shape = cfg.roster_shape
    expected_starters = shape.qb + shape.rb + shape.wr + shape.te + shape.flex
    if expected_starters != len(cfg.starter_slots):
        raise ConfigError(
            f"roster_shape describes {expected_starters} starters but slots list "
            f"{len(cfg.starter_slots)}: {list(cfg.starter_slots)}"
        )
    if shape.bench_min > len(cfg.bench_slots):
        raise ConfigError(
            f"roster_shape.bench_min ({shape.bench_min}) exceeds the "
            f"{len(cfg.bench_slots)} bench slots that actually get scored"
        )
