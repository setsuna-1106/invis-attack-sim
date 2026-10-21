"""Whole-population first-event statistics; missing events keep their mass."""

from dataclasses import dataclass
import math

from .config import Config


@dataclass(frozen=True)
class Trial:
    trial_id: int
    event_time: float | None
    stop_time: float
    stop_reason: str
    attacker: int | None = None


def histogram(trials: list[Trial], config: Config) -> list[dict]:
    if not trials:
        raise ValueError("at least one trial is required")
    bins = round(config.horizon / config.bin_width)
    counts = [0] * bins
    for trial in trials:
        if trial.event_time is not None:
            if not math.isfinite(trial.event_time) or not 0 <= trial.event_time <= config.horizon:
                raise ValueError("event outside observation window")
            index = min(int(trial.event_time / config.bin_width), bins - 1)
            counts[index] += 1
    n = len(trials)
    return [dict(start_s=i * config.bin_width, end_s=(i + 1) * config.bin_width,
                 count=count, probability=count / n, density_per_s=count / (n * config.bin_width))
            for i, count in enumerate(counts)]


def cdf_distance(a: list[Trial], b: list[Trial]) -> float:
    """Exact empirical CDF distance, including jumps within histogram bins."""
    if not a or not b:
        raise ValueError("CDF comparison requires two nonempty samples")
    x = sorted(t.event_time for t in a if t.event_time is not None)
    y = sorted(t.event_time for t in b if t.event_time is not None)
    i = j = 0
    distance = 0.0
    while i < len(x) or j < len(y):
        time = min(x[i] if i < len(x) else math.inf, y[j] if j < len(y) else math.inf)
        while i < len(x) and x[i] == time:
            i += 1
        while j < len(y) and y[j] == time:
            j += 1
        distance = max(distance, abs(i / len(a) - j / len(b)))
    return distance


def stability_bound(n: int, m: int) -> float:
    return math.sqrt(math.log(80) / (2 * n)) + math.sqrt(math.log(80) / (2 * m))
