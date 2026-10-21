"""All-attack event intensities and interval probabilities over complete trials."""

from dataclasses import dataclass
import math

from .config import Config


@dataclass(frozen=True)
class Attack:
    time: float
    attacker: int


@dataclass(frozen=True)
class Trial:
    trial_id: int
    attacks: tuple[Attack, ...]
    stop_time: float
    stop_reason: str

    @property
    def event_time(self) -> float | None:
        """First attack time, retained only as a per-trial diagnostic."""
        return self.attacks[0].time if self.attacks else None


def histogram(trials: list[Trial], config: Config) -> list[dict]:
    if not trials:
        raise ValueError("at least one trial is required")
    bins = round(config.horizon / config.bin_width)
    counts = [0] * bins
    hit_trials = [0] * bins
    for trial in trials:
        if not math.isfinite(trial.stop_time) or not 0 <= trial.stop_time <= config.horizon:
            raise ValueError("invalid trial stop time")
        hit_bins = set()
        previous = -math.inf
        for attack in trial.attacks:
            if not math.isfinite(attack.time) or not previous <= attack.time <= trial.stop_time:
                raise ValueError("attacks must be ordered and within the observation window")
            if attack.time < 0:
                raise ValueError("attack outside observation window")
            previous = attack.time
            index = min(int(attack.time / config.bin_width), bins - 1)
            counts[index] += 1
            hit_bins.add(index)
        for index in hit_bins:
            hit_trials[index] += 1
    n = len(trials)
    return [dict(start_s=i * config.bin_width, end_s=(i + 1) * config.bin_width,
                 count=count, mean_count=count / n,
                 density_per_s=count / (n * config.bin_width),
                 hit_trials=hit_trials[i], hit_probability=hit_trials[i] / n)
            for i, count in enumerate(counts)]


def mean_count_curve(trials: list[Trial], config: Config) -> list[float]:
    cumulative = 0.0
    curve = []
    for row in histogram(trials, config):
        cumulative += row["mean_count"]
        curve.append(cumulative)
    return curve


def count_curve_distance(a: list[Trial], b: list[Trial], config: Config) -> float:
    return max(abs(x - y) for x, y in zip(mean_count_curve(a, config), mean_count_curve(b, config)))


def count_stability(a: list[Trial], b: list[Trial], config: Config, *,
                    seed: int, repetitions: int = 400) -> dict:
    """Whole-trial bootstrap band for the difference in mean count curves.

    Events from the same match are correlated. Resample complete match histories,
    not individual attacks. The 95% band is an approximate bootstrap diagnostic
    over the configured bin edges, not the DKW bound for independent first events.
    """
    import numpy as np

    if type(repetitions) is not int or repetitions < 20:
        raise ValueError("at least 20 bootstrap repetitions are required")
    curves = [np.array(mean_count_curve(group, config)) for group in (a, b)]
    bins = len(curves[0])
    prepared = []
    for group in (a, b):
        owners = []
        indices = []
        for owner, trial in enumerate(group):
            for attack in trial.attacks:
                owners.append(owner)
                indices.append(min(int(attack.time / config.bin_width), bins - 1))
        prepared.append((np.array(owners, dtype=np.intp), np.array(indices, dtype=np.intp),
                         np.full(len(group), 1 / len(group))))
    rng = np.random.default_rng(seed)
    maxima = []
    for _ in range(repetitions):
        deviations = []
        for group, curve, (owners, indices, probabilities) in zip((a, b), curves, prepared):
            weights = rng.multinomial(len(group), probabilities)
            counts = np.bincount(indices, weights=weights[owners], minlength=bins)
            deviations.append(np.cumsum(counts) / len(group) - curve)
        maxima.append(float(np.max(np.abs(deviations[0] - deviations[1]))))
    distance = float(np.max(np.abs(curves[0] - curves[1])))
    threshold = float(np.quantile(maxima, .95, method="higher"))
    return dict(distance=distance, threshold_95=threshold, passed=distance <= threshold,
                bootstrap_seed=seed, bootstrap_repetitions=repetitions,
                method="Approximate 95% simultaneous whole-trial bootstrap band for mean cumulative attack counts at bin edges; attacks within a trial are resampled together.")
