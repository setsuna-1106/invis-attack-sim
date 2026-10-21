"""Portable all-attack CSV/JSON results and headless Matplotlib figures."""

from collections import Counter
from collections.abc import Iterable
import csv
import hashlib
import json
import math
from pathlib import Path
import platform

from .config import Config
from .engine import MODEL_VERSION
from .results import Trial, histogram, mean_count_curve


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_csv(path: Path, rows: Iterable[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def plot_density(rows: list[dict], summary: dict, output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    available = {font.name for font in font_manager.fontManager.ttflist}
    chinese_font = next((name for name in ("PingFang SC", "Noto Sans CJK SC",
                                          "Microsoft YaHei", "Arial Unicode MS")
                         if name in available), None)
    style = {"font.size": 11, "axes.spines.top": False, "axes.spines.right": False}
    if chinese_font:
        style["font.family"] = chinese_font
    edges = [row["start_s"] / 60 for row in rows] + [rows[-1]["end_s"] / 60]
    width = rows[0]["end_s"] - rows[0]["start_s"]
    if chinese_font:
        xlabel = "开局后的时间（分钟）"
        titles = ("不同时刻受到隐身攻击的事件密度（计入所有次数）",
                  f"各 {width:g} 秒区间内至少受到一次隐身攻击的概率")
        ylabels = ("攻击事件密度［次 /（局·秒）］", "至少一次攻击的概率")
        note = (f"模拟 {summary['trials']:,} 局 · 分箱 {width:g} 秒\n"
                f"每局平均 {summary['mean_attacks']:.3f} 次 · "
                f"零攻击对局 {summary['none_probability']:.2%}")
    else:
        xlabel = "Time since game start (min)"
        titles = ("Invisible attack intensity over time (all attacks)",
                  f"Probability of at least one invisible attack per {width:g}s interval")
        ylabels = ("Attack intensity (attacks / trial / s)", "Probability of at least one attack")
        note = (f"N={summary['trials']:,} | bin={width:g}s\n"
                f"Mean attacks={summary['mean_attacks']:.3f} | "
                f"Zero-attack trials={summary['none_probability']:.2%}")
    series = ([row["density_per_s"] for row in rows],
              [row["hit_probability"] for row in rows])
    with plt.rc_context(style):
        def draw(axis, index):
            axis.stairs(series[index], edges, fill=True, alpha=.5, color="#28668c", linewidth=.9)
            axis.set(title=titles[index], ylabel=ylabels[index], xlim=(0, edges[-1]), ylim=(0, None))
            axis.grid(axis="y", alpha=.2)

        fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, layout="constrained")
        for index, axis in enumerate(axes):
            draw(axis, index)
        axes[0].text(.98, .95, note, transform=axes[0].transAxes, ha="right", va="top")
        axes[-1].set_xlabel(xlabel)
        fig.savefig(output / "density.png", dpi=160)
        fig.savefig(output / "density.svg")
        plt.close(fig)
        for index, filename in enumerate(("attack_density", "interval_probability")):
            fig, axis = plt.subplots(figsize=(11, 5), layout="constrained")
            draw(axis, index)
            axis.set_xlabel(xlabel)
            axis.text(.98, .95, note, transform=axis.transAxes, ha="right", va="top")
            fig.savefig(output / f"{filename}.png", dpi=180)
            fig.savefig(output / f"{filename}.svg")
            plt.close(fig)


def export_run(trials: list[Trial], config: Config, output: Path,
               elapsed: float, *, plot: bool = True) -> dict:
    if len(trials) != config.trials:
        raise ValueError("trial count does not match configuration")
    rows = histogram(trials, config)
    none = sum(not trial.attacks for trial in trials)
    total_attacks = sum(len(trial.attacks) for trial in trials)
    mean_attacks = total_attacks / len(trials)
    integral = sum(row["density_per_s"] * config.bin_width for row in rows)
    if not math.isclose(integral, mean_attacks, rel_tol=1e-12, abs_tol=1e-12):
        raise RuntimeError("attack intensity integral must equal mean attacks per trial")
    summary = dict(schema_version=2, model_version=MODEL_VERSION, source_sha256=source_fingerprint(),
                   python_version=platform.python_version(), config=config.to_dict(),
                   trials=len(trials), seed=config.seed, total_attacks=total_attacks,
                   attacked_trials=len(trials) - none, zero_attack_trials=none,
                   mean_attacks=mean_attacks, none_probability=none / len(trials),
                   ever_attacked_probability=1 - none / len(trials),
                   density_integral=integral,
                   probability_total=(len(trials) - none) / len(trials) + none / len(trials),
                   stop_reasons=dict(Counter(t.stop_reason for t in trials)),
                   runtime_seconds=elapsed,
                   intensity_definition="All attacks in a bin / (all trials * bin width). Integral is mean attack count, not a probability. hit_probability counts each trial at most once per bin.",
                   interpretation="Continue after every attack; stop at own bed loss, elimination, game end or horizon. Simplified model, not calibrated to real matches.")
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "histogram.csv", rows, list(rows[0]))
    write_csv(output / "trials.csv", (dict(trial_id=trial.trial_id, attack_count=len(trial.attacks),
              first_attack_time=trial.event_time, last_attack_time=trial.attacks[-1].time if trial.attacks else None,
              stop_time=trial.stop_time, stop_reason=trial.stop_reason) for trial in trials),
              ["trial_id", "attack_count", "first_attack_time", "last_attack_time", "stop_time", "stop_reason"])
    write_csv(output / "attacks.csv", (dict(trial_id=trial.trial_id, attack_number=number,
              time_s=attack.time, attacker=attack.attacker) for trial in trials
              for number, attack in enumerate(trial.attacks, 1)),
              ["trial_id", "attack_number", "time_s", "attacker"])
    write_json(output / "config.json", config.to_dict())
    write_json(output / "summary.json", summary)
    if plot:
        plot_density(rows, summary, output)
    return summary


def plot_stability(groups: list[list[Trial]], report: dict, output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 5), layout="constrained")
    for trials, run in zip(groups, report["runs"]):
        config = Config(**report["config"])
        edges = [i * config.bin_width / 60 for i in range(round(config.horizon / config.bin_width) + 1)]
        ax.step(edges, [0] + mean_count_curve(trials, config),
                where="post", label=f"N={len(trials):,}, seed={run['seed']}", linewidth=1.1)
    ax.set(xlabel="Time since game start (min)", ylabel="Mean cumulative attack count per trial",
           title=f"All-attack Monte Carlo stability: {'PASS' if report['passed'] else 'FAIL'}",
           xlim=(0, report["horizon"] / 60), ylim=(0, None))
    ax.legend(loc="lower right")
    ax.grid(alpha=.2)
    fig.savefig(output / "stability.png", dpi=160)
    plt.close(fig)
