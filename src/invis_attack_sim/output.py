"""Portable CSV/JSON results and Matplotlib figures (headless backend)."""

from collections import Counter
import csv
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import platform

from .config import Config
from .engine import MODEL_VERSION
from .results import Trial, histogram


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def plot_density(rows: list[dict], summary: dict, output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    edges = [row["start_s"] / 60 for row in rows] + [rows[-1]["end_s"] / 60]
    density = [row["density_per_s"] for row in rows]
    cumulative = []
    mass = 0.0
    for row in rows:
        mass += row["probability"]
        cumulative.append(mass)
    with plt.rc_context({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False}):
        fig, (ax, cdf) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True,
                                      gridspec_kw={"height_ratios": [2, 1]}, layout="constrained")
        ax.stairs(density, edges, fill=True, alpha=.4, color="#28668c", linewidth=.8)
        ax.set(ylabel="Probability density (1/s)", title="First invisible entry toward our base")
        ax.text(.98, .95, f"N = {summary['trials']:,}  |  seed = {summary['seed']}\n"
                f"No event before stop: {summary['none_probability']:.2%}",
                transform=ax.transAxes, ha="right", va="top")
        cdf.step(edges, [0] + cumulative, where="post", color="#28668c")
        cdf.set(xlabel="Time since game start (min)", ylabel="Cumulative probability",
                xlim=(0, edges[-1]), ylim=(0, 1))
        for axis in (ax, cdf):
            axis.grid(axis="y", alpha=.2)
        fig.savefig(output / "density.png", dpi=160)
        fig.savefig(output / "density.svg")
        plt.close(fig)

    # A separate density figure makes the time-dependent first-event rate
    # directly usable without the cumulative-probability panel.
    from matplotlib import font_manager
    available = {font.name for font in font_manager.fontManager.ttflist}
    chinese_font = next((name for name in ("PingFang SC", "Noto Sans CJK SC",
                                          "Microsoft YaHei", "Arial Unicode MS")
                         if name in available), None)
    style = {"font.size": 11, "axes.spines.top": False, "axes.spines.right": False}
    if chinese_font:
        style["font.family"] = chinese_font
    with plt.rc_context(style):
        fig, ax = plt.subplots(figsize=(11, 5), layout="constrained")
        ax.stairs(density, edges, fill=True, alpha=.5, color="#28668c", linewidth=.9)
        width = rows[0]["end_s"] - rows[0]["start_s"]
        if chinese_font:
            title = "不同时刻首次受到隐身攻击的概率密度"
            xlabel, ylabel = "开局后的时间（分钟）", "概率密度（每秒）"
            annotation = (f"模拟 {summary['trials']:,} 局 · 分箱 {width:g} 秒\n"
                          f"遭遇概率 {summary['density_integral']:.2%} · "
                          f"未遭遇概率 {summary['none_probability']:.2%}")
        else:
            title = "First invisible attack: probability density over time"
            xlabel, ylabel = "Time since game start (min)", "Probability density (1/s)"
            annotation = (f"N={summary['trials']:,} | bin={width:g}s\n"
                          f"Event: {summary['density_integral']:.2%} | "
                          f"No event: {summary['none_probability']:.2%}")
        ax.set(title=title, xlabel=xlabel, ylabel=ylabel, xlim=(0, edges[-1]), ylim=(0, None))
        ax.text(.98, .95, annotation, transform=ax.transAxes, ha="right", va="top")
        ax.grid(axis="y", alpha=.2)
        fig.savefig(output / "attack_density.png", dpi=180)
        fig.savefig(output / "attack_density.svg")
        plt.close(fig)


def export_run(trials: list[Trial], config: Config, output: Path,
               elapsed: float, *, plot: bool = True) -> dict:
    if len(trials) != config.trials:
        raise ValueError("trial count does not match configuration")
    rows = histogram(trials, config)
    none = sum(t.event_time is None for t in trials)
    integral = sum(row["density_per_s"] * config.bin_width for row in rows)
    total = integral + none / len(trials)
    if not math.isclose(total, 1, abs_tol=1e-12):
        raise RuntimeError("probability normalization failed")
    summary = dict(model_version=MODEL_VERSION, source_sha256=source_fingerprint(),
                   python_version=platform.python_version(), config=config.to_dict(),
                   trials=len(trials), seed=config.seed, events=len(trials) - none,
                   no_events=none, none_probability=none / len(trials),
                   density_integral=integral, probability_total=total,
                   stop_reasons=dict(Counter(t.stop_reason for t in trials)),
                   runtime_seconds=elapsed,
                   interpretation="Simplified model; no-event mass includes early bed loss and game end. Not calibrated to real matches.")
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "histogram.csv", rows, list(rows[0]))
    write_csv(output / "trials.csv", [asdict(trial) for trial in trials],
              ["trial_id", "event_time", "stop_time", "stop_reason", "attacker"])
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
        times = sorted(t.event_time / 60 for t in trials if t.event_time is not None)
        ax.step([0] + times + [report["horizon"] / 60],
                [0] + [(i + 1) / len(trials) for i in range(len(times))] + [len(times) / len(trials)],
                where="post", label=f"N={len(trials):,}, seed={run['seed']}", linewidth=1.1)
    ax.set(xlabel="Time since game start (min)", ylabel="Cumulative probability",
           title=f"Monte Carlo stability: {'PASS' if report['passed'] else 'FAIL'}",
           xlim=(0, report["horizon"] / 60), ylim=(0, 1))
    ax.legend(loc="lower right")
    ax.grid(alpha=.2)
    fig.savefig(output / "stability.png", dpi=160)
    plt.close(fig)
