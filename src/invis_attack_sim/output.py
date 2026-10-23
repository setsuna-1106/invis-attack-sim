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
        note = (f"模拟 {summary['complete_trials']:,} 局 · 分箱 {width:g} 秒\n"
                f"每局平均 {summary['mean_attacks']:.3f} 次 · "
                f"零攻击对局 {summary['none_probability']:.2%}")
    else:
        xlabel = "Time since game start (min)"
        titles = ("Invisible attack intensity over time (all attacks)",
                  f"Probability of at least one invisible attack per {width:g}s interval")
        ylabels = ("Attack intensity (attacks / trial / s)", "Probability of at least one attack")
        note = (f"N={summary['complete_trials']:,} | bin={width:g}s\n"
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
    complete = [t for t in trials if t.complete]
    n = len(complete)
    none = sum(not trial.attacks for trial in complete)
    total_attacks = sum(len(trial.attacks) for trial in complete)
    mean_attacks = total_attacks / n if n else None
    integral = sum(row["density_per_s"] * config.bin_width for row in rows) if n else None
    if n and not math.isclose(integral, mean_attacks, rel_tol=1e-12, abs_tol=1e-12):
        raise RuntimeError("attack intensity integral must equal mean attacks per complete trial")
    summary = dict(schema_version=3, model_version=MODEL_VERSION, source_sha256=source_fingerprint(),
                   python_version=platform.python_version(), config=config.to_dict(),
                   trials=len(trials), complete_trials=n, incomplete_trials=len(trials) - n,
                   all_complete=n == len(trials), seed=config.seed, total_attacks=total_attacks,
                   attempted_total_attacks=sum(len(t.attacks) for t in trials),
                   attacked_trials=n - none, zero_attack_trials=none,
                   mean_attacks=mean_attacks, none_probability=none / n if n else None,
                   ever_attacked_probability=1 - none / n if n else None,
                   density_integral=integral, probability_total=1.0 if n else None,
                   event_stages=dict(Counter(a.stage for t in complete for a in t.attacks)),
                   continued_pursuits=sum(a.continued_pursuit for t in complete for a in t.attacks),
                   observation_end_s=max((t.stop_time for t in complete), default=None),
                   axis_end_s=rows[-1]["end_s"] if rows else None,
                   stop_reasons=dict(Counter(t.stop_reason for t in trials)),
                   excluded_reasons=dict(Counter(t.stop_reason for t in trials if not t.complete)),
                   runtime_seconds=elapsed,
                   intensity_definition="All valid events / (complete trials * full bin width). Integral is mean count, not probability; each episode counts once. Incomplete trials excluded, never zero-filled.",
                   interpretation="Observe until own elimination or game end, including after bed loss and 30 min. Technical guards are incomplete. Simplified model; not calibrated to real matches.")
    output.mkdir(parents=True, exist_ok=True)
    histogram_fields = list(rows[0]) if rows else ["start_s", "end_s", "count", "mean_count", "density_per_s", "base_count", "pursuit_count", "base_density_per_s", "pursuit_density_per_s", "attacker_bed_count", "attacker_no_bed_count", "complete_trials", "hit_trials", "hit_probability"]
    write_csv(output / "histogram.csv", rows, histogram_fields)
    write_csv(output / "trials.csv", (dict(trial_id=t.trial_id, complete=t.complete, attack_count=len(t.attacks),
              first_attack_time=t.event_time, last_attack_time=t.attacks[-1].time if t.attacks else None,
              bed_loss_time=t.bed_loss_time, stop_time=t.stop_time, stop_reason=t.stop_reason) for t in trials),
              ["trial_id", "complete", "attack_count", "first_attack_time", "last_attack_time", "bed_loss_time", "stop_time", "stop_reason"])
    write_csv(output / "attacks.csv", (dict(trial_id=t.trial_id, complete=t.complete, attack_number=number,
              time_s=a.time, attacker=a.attacker, episode=a.episode, stage=a.stage,
              own_bed=a.stage == "base", attacker_bed=a.attacker_bed,
              continued_pursuit=a.continued_pursuit) for t in trials for number, a in enumerate(t.attacks, 1)),
              ["trial_id", "complete", "attack_number", "time_s", "attacker", "episode", "stage", "own_bed", "attacker_bed", "continued_pursuit"])
    write_json(output / "config.json", config.to_dict())
    write_json(output / "summary.json", summary)
    if plot:
        if rows:
            plot_density(rows, summary, output)
        else:
            # A missing complete sample is not a zero-density curve. Also
            # replace old figures if the output directory is reused.
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(9, 4))
            ax.axis("off")
            ax.text(.5, .5, "No complete trials.\nPrimary estimates unavailable; inspect guard reasons.",
                    ha="center", va="center")
            for name in ("density", "attack_density", "interval_probability"):
                for ext in ("png", "svg"):
                    fig.savefig(output / f"{name}.{ext}")
            plt.close(fig)
    return summary


def plot_stability(groups: list[list[Trial]], report: dict, output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 5), layout="constrained")
    for trials, run in zip(groups, report["runs"]):
        config = Config(**report["config"])
        curve = mean_count_curve(trials, config, end_time=report["axis_end_s"])
        edges = [i * config.bin_width / 60 for i in range(len(curve) + 1)]
        ax.step(edges, [0] + curve,
                where="post", label=f"N={len(trials):,}, seed={run['seed']}", linewidth=1.1)
    ax.set(xlabel="Time since game start (min)", ylabel="Mean cumulative attack count per trial",
           title=f"All-attack Monte Carlo stability: {'PASS' if report['passed'] else 'FAIL'}",
           xlim=(0, report["axis_end_s"] / 60), ylim=(0, None))
    ax.legend(loc="lower right")
    ax.grid(alpha=.2)
    fig.savefig(output / "stability.png", dpi=160)
    plt.close(fig)
