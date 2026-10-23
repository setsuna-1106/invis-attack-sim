"""Validated inputs for the simplified four-team model (times in seconds)."""

from dataclasses import asdict, dataclass, fields
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Config:
    trials: int = 10_000
    seed: int = 42
    max_time: float | None = None
    max_events: int = 100_000
    bin_width: float = 5.0
    our_team: int = 0
    walk_time: float = 10.0
    build_time: float = 60.0
    respawn_time: float = 5.0
    boundary: float = 0.25
    potion_cost: int = 2
    invis_duration: float = 30.0
    generator_cap: int = 4
    spawn_periods: tuple[float, ...] = (56.0, 40.0, 28.0)
    upgrade_times: tuple[float, ...] = (360.0, 1080.0)
    collection_start: float = 0.8
    collection_end: float = 0.3
    invis_share_start: float = 0.2
    invis_share_end: float = 0.7
    collection_floor: float = 0.2
    invis_share_ceiling: float = 0.8
    policy_plateau_time: float = 2160.0
    approach_timeout: float = 30.0
    engagement_range: float = 2.5
    contested_wait: tuple[float, float] = (10.0, 90.0)
    encounter_wait: tuple[float, float] = (2.0, 5.0)
    break_time: tuple[float, float] = (15.0, 40.0)
    pursuit_time: tuple[float, float] = (5.0, 20.0)
    normal_break_chance: float = 0.08
    normal_break_chance_end: float = 0.03
    invis_break_chance: float = 0.6
    invis_break_chance_end: float = 0.4
    no_bed_invis_odds_multiplier: float = 2.0
    no_bed_failure_death_multiplier: float = 0.5
    death_chance: float = 0.6
    pursue_chance: float = 0.5
    kill_chance: float = 0.6

    def __post_init__(self) -> None:
        for name in ("trials", "seed", "our_team", "potion_cost", "generator_cap", "max_events"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        if self.trials < 1 or self.seed < 0 or not 0 <= self.our_team < 4:
            raise ValueError("trials >= 1, seed >= 0 and 0 <= our_team < 4 required")
        if self.potion_cost < 1 or self.generator_cap < 1:
            raise ValueError("potion_cost and generator_cap must be positive")
        if self.max_events < 1:
            raise ValueError("max_events must be positive")
        if self.max_time is not None and (isinstance(self.max_time, bool) or not isinstance(self.max_time, (int, float)) or not math.isfinite(self.max_time) or self.max_time <= 0):
            raise ValueError("max_time must be null or finite and positive (technical guard only)")
        for name in ("bin_width", "walk_time", "build_time", "respawn_time", "invis_duration", "policy_plateau_time", "approach_timeout", "engagement_range"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.policy_plateau_time <= 1800:
            raise ValueError("policy_plateau_time must be after 30 minutes")
        if self.build_time < self.walk_time:
            raise ValueError("build_time must include at least walk_time")
        for name in ("boundary", "collection_start", "collection_end", "collection_floor", "invis_share_start", "invis_share_end", "invis_share_ceiling", "normal_break_chance", "normal_break_chance_end", "invis_break_chance", "invis_break_chance_end", "no_bed_failure_death_multiplier", "death_chance", "pursue_chance", "kill_chance"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        multiplier = self.no_bed_invis_odds_multiplier
        if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)) or not math.isfinite(multiplier) or multiplier <= 0:
            raise ValueError("no_bed_invis_odds_multiplier must be finite and positive")
        if (self.normal_break_chance == 0) != (self.normal_break_chance_end == 0):
            raise ValueError("exponential normal break endpoints must both be positive or both zero")
        if not 0 < self.boundary < 1:
            raise ValueError("boundary must be inside the route")
        for name, length in (("spawn_periods", 3), ("upgrade_times", 2), ("contested_wait", 2), ("encounter_wait", 2), ("break_time", 2), ("pursuit_time", 2)):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or len(values) != length:
                raise ValueError(f"{name} requires {length} values")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values):
                raise ValueError(f"{name} values must be finite and positive")
            if name != "spawn_periods" and values[0] > values[1]:
                raise ValueError(f"{name} must be ordered")
            object.__setattr__(self, name, tuple(values))
        if self.upgrade_times[0] == self.upgrade_times[1]:
            raise ValueError("upgrade times must be distinct")

    def weights(self, time: float) -> tuple[float, float, float]:
        # The policy is tied to game time, even for a shorter observation horizon.
        if time <= 1800:
            fraction = max(0.0, time / 1800)
            collect = self.collection_start + (self.collection_end - self.collection_start) * fraction
            invis = self.invis_share_start + (self.invis_share_end - self.invis_share_start) * fraction
        else:
            fraction = min(1.0, (time - 1800) / (self.policy_plateau_time - 1800))
            collect = self.collection_end + (self.collection_floor - self.collection_end) * fraction
            invis = self.invis_share_end + (self.invis_share_ceiling - self.invis_share_end) * fraction
        return collect, (1 - collect) * (1 - invis), (1 - collect) * invis

    def to_dict(self) -> dict:
        return asdict(self)

    def break_chance(self, time: float, *, invisible: bool) -> float:
        """Conditional success at resolution; endpoints refer to 0 and 30 min."""
        fraction = min(1.0, max(0.0, time / 1800))
        if invisible:
            return self.invis_break_chance + (self.invis_break_chance_end - self.invis_break_chance) * fraction
        if self.normal_break_chance == 0:
            return 0.0
        # Interpolate in log space to avoid underflow in the endpoint ratio.
        return math.exp((1 - fraction) * math.log(self.normal_break_chance)
                        + fraction * math.log(self.normal_break_chance_end))

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("configuration must be a JSON object")
        unknown = data.keys() - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
        return cls(**data)
