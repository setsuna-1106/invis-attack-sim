"""All-attack event intensities and interval probabilities over complete trials."""

from dataclasses import dataclass
import math

from .config import Config


@dataclass(frozen=True)
class Attack:
    time: float
    attacker: int
    episode: int | None = None
    stage: str = "base"
    attacker_bed: bool = True
    continued_pursuit: bool = False


@dataclass(frozen=True)
class Trial:
    trial_id: int
    attacks: tuple[Attack, ...]
    stop_time: float
    stop_reason: str
    bed_loss_time: float | None = None

    @property
    def complete(self) -> bool:
        return self.stop_reason in ("eliminated", "game_end")

    @property
    def event_time(self) -> float | None:
        """First attack time, retained only as a per-trial diagnostic."""
        return self.attacks[0].time if self.attacks else None


def histogram(trials: list[Trial], config: Config, *, end_time: float | None = None) -> list[dict]:
    if not trials:
        raise ValueError("at least one trial is required")
    complete = [t for t in trials if t.complete]
    endpoint = max((t.stop_time for t in complete), default=0) if end_time is None else end_time
    if not math.isfinite(endpoint) or endpoint < max((t.stop_time for t in complete), default=0):
        raise ValueError("common time axis must cover all complete observation endpoints")
    bins = max(1, math.ceil(endpoint / config.bin_width))
    counts = [0] * bins
    hit_trials = [0] * bins
    base = [0] * bins
    attacker_bed = [0] * bins
    for trial in trials:
        if not math.isfinite(trial.stop_time) or trial.stop_time < 0:
            raise ValueError("invalid trial stop time")
        hit_bins = set()
        previous = -math.inf
        for attack in trial.attacks:
            if not math.isfinite(attack.time) or not previous <= attack.time <= trial.stop_time:
                raise ValueError("attacks must be ordered and within the observation window")
            if attack.time < 0:
                raise ValueError("attack outside observation window")
            previous = attack.time
            if not trial.complete:
                continue
            if attack.stage not in ("base", "pursuit") or attack.time > bins * config.bin_width:
                raise ValueError("invalid event stage or common time axis")
            index = min(int(attack.time / config.bin_width), bins - 1)
            counts[index] += 1
            base[index] += attack.stage == "base"
            attacker_bed[index] += attack.attacker_bed
            hit_bins.add(index)
        for index in hit_bins:
            hit_trials[index] += 1
    n = len(complete)
    if not n:
        return []  # An unknown continuation cannot be represented as zero events.
    return [dict(start_s=i * config.bin_width, end_s=(i + 1) * config.bin_width,
                 count=count, mean_count=count / n,
                 density_per_s=count / (n * config.bin_width),
                 base_count=base[i], pursuit_count=count - base[i],
                 base_density_per_s=base[i] / (n * config.bin_width),
                 pursuit_density_per_s=(count - base[i]) / (n * config.bin_width),
                 attacker_bed_count=attacker_bed[i], attacker_no_bed_count=count - attacker_bed[i],
                 complete_trials=n,
                 hit_trials=hit_trials[i], hit_probability=hit_trials[i] / n)
            for i, count in enumerate(counts)]


def mean_count_curve(trials: list[Trial], config: Config, *, end_time: float | None = None) -> list[float]:
    cumulative = 0.0
    curve = []
    for row in histogram(trials, config, end_time=end_time):
        cumulative += row["mean_count"]
        curve.append(cumulative)
    return curve


def count_curve_distance(a: list[Trial], b: list[Trial], config: Config) -> float:
    endpoint = max(t.stop_time for t in a + b if t.complete)
    curves = [mean_count_curve(g, config, end_time=endpoint) for g in (a, b)]
    if not all(curves):
        raise ValueError("complete trials required for comparison")
    return max(abs(x - y) for x, y in zip(*curves))


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
    if not a or not b or not all(t.complete for t in a + b):
        raise ValueError("stability requires fully completed runs; rerun or diagnose guards")
    endpoint = max(t.stop_time for t in a + b)
    curves = [np.array(mean_count_curve(group, config, end_time=endpoint)) for group in (a, b)]
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
