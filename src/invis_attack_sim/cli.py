"""Command-line simulation and independent-sample stability checks."""

import argparse
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from .config import Config
from .engine import MODEL_VERSION, simulate
from .output import export_run, plot_stability, source_fingerprint, write_json
from .results import count_curve_distance, count_stability, histogram


def run_stability(config: Config, sizes: list[int], output: Path, *, audit: bool = False,
                  plot: bool = True, bootstrap_repetitions: int = 400) -> dict:
    if len(sizes) != 3 or any(n < 1 for n in sizes) or not sizes[0] < sizes[1] < sizes[2]:
        raise ValueError("stability requires three strictly increasing positive sample sizes")
    if type(bootstrap_repetitions) is not int or bootstrap_repetitions < 20:
        raise ValueError("at least 20 bootstrap repetitions are required")
    groups = []
    runs = []
    for offset, size in enumerate(sizes):
        current = replace(config, trials=size, seed=config.seed + offset)
        start = perf_counter()
        trials = simulate(current, validate=audit)
        elapsed = perf_counter() - start
        summary = export_run(trials, current, output / f"n{size}-seed{current.seed}", elapsed, plot=plot)
        groups.append(trials)
        runs.append(dict(trials=size, seed=current.seed, runtime_seconds=elapsed,
                         none_probability=summary["none_probability"], mean_attacks=summary["mean_attacks"],
                         probability_total=summary["probability_total"]))
        if not summary["all_complete"]:
            report = dict(model_version=MODEL_VERSION, config=config.to_dict(), runs=runs,
                          passed=False, reason="incomplete trials; stability unavailable",
                          incomplete_trials=summary["incomplete_trials"], distance=None, threshold_95=None)
            write_json(output / "stability.json", report)
            return report
        print(f"N={size:,}, seed={current.seed}: mean-attacks={summary['mean_attacks']:.6f}, zero-attacks={summary['none_probability']:.6f}, {elapsed:.2f}s", flush=True)
    comparison = count_stability(groups[-2], groups[-1], config, seed=config.seed + 3,
                                 repetitions=bootstrap_repetitions)
    report = dict(model_version=MODEL_VERSION, source_sha256=source_fingerprint(),
                  config=config.to_dict(), axis_end_s=histogram([t for g in groups for t in g], config)[-1]["end_s"], runs=runs,
                  state_audits=audit,
                  count_curve_distances=[dict(n=sizes[i], m=sizes[i + 1],
                      distance=count_curve_distance(groups[i], groups[i + 1], config)) for i in range(2)],
                  **comparison)
    write_json(output / "stability.json", report)
    if plot:
        plot_stability(groups, report, output)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Four-team all-attack invisible entry simulation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "stability"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("configs/default.json"))
        command.add_argument("--output", type=Path, default=Path("outputs") / name)
        command.add_argument("--seed", type=int)
        command.add_argument("--max-time", type=float, help="technical time guard; truncations are incomplete")
        command.add_argument("--bin-width", type=float, help="output bin width only, independent of movement")
        command.add_argument("--audit", action="store_true", help="check state invariants after each event")
        if name == "run":
            command.add_argument("--trials", type=int)
        else:
            command.add_argument("--sizes", type=int, nargs=3, default=[10_000, 40_000, 160_000])
            command.add_argument("--bootstrap-repetitions", type=int, default=400)
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        if args.seed is not None:
            config = replace(config, seed=args.seed)
        if args.max_time is not None:
            config = replace(config, max_time=args.max_time)
        if args.bin_width is not None:
            config = replace(config, bin_width=args.bin_width)
        if args.command == "run":
            if args.trials is not None:
                config = replace(config, trials=args.trials)
            start = perf_counter()
            trials = simulate(config, validate=args.audit)
            summary = export_run(trials, config, args.output, perf_counter() - start)
            print(f"{summary['trials']:,} attempted; complete={summary['complete_trials']:,}; "
                  f"incomplete={summary['incomplete_trials']:,}; attacks={summary['total_attacks']:,}; "
                  f"mean-attacks={summary['mean_attacks']}; output={args.output}")
            if not summary["all_complete"]:
                return 2
        else:
            report = run_stability(config, args.sizes, args.output, audit=args.audit,
                                   bootstrap_repetitions=args.bootstrap_repetitions)
            if report["distance"] is None:
                print(report["reason"])
                return 2
            print(f"Stability {'PASS' if report['passed'] else 'FAIL'}: "
                  f"Mean-count curve distance={report['distance']:.6f}, bound={report['threshold_95']:.6f}")
            if not report["passed"]:
                return 1
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    return 0
