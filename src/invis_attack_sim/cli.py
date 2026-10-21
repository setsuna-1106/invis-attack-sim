"""Command-line simulation and independent-sample stability checks."""

import argparse
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from .config import Config
from .engine import MODEL_VERSION, simulate
from .output import export_run, plot_stability, source_fingerprint, write_json
from .results import cdf_distance, stability_bound


def run_stability(config: Config, sizes: list[int], output: Path, *, audit: bool = False,
                  plot: bool = True) -> dict:
    if len(sizes) != 3 or any(n < 1 for n in sizes) or not sizes[0] < sizes[1] < sizes[2]:
        raise ValueError("stability requires three strictly increasing positive sample sizes")
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
                         none_probability=summary["none_probability"],
                         probability_total=summary["probability_total"]))
        print(f"N={size:,}, seed={current.seed}: no-event={summary['none_probability']:.6f}, {elapsed:.2f}s", flush=True)
    distance = cdf_distance(groups[-2], groups[-1])
    threshold = stability_bound(sizes[-2], sizes[-1])
    report = dict(model_version=MODEL_VERSION, source_sha256=source_fingerprint(),
                  config=config.to_dict(), horizon=config.horizon, runs=runs,
                  state_audits=audit,
                  cdf_distances=[dict(n=sizes[i], m=sizes[i + 1],
                                      distance=cdf_distance(groups[i], groups[i + 1])) for i in range(2)],
                  final_cdf_distance=distance, threshold_95=threshold,
                  passed=distance <= threshold,
                  method="Exact empirical sub-CDF sup distance; DKW union bound with log(80). Missing events remain mass at infinity; final two independent samples determine pass/fail.")
    write_json(output / "stability.json", report)
    if plot:
        plot_stability(groups, report, output)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Four-team invisible first-entry simulation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "stability"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("configs/default.json"))
        command.add_argument("--output", type=Path, default=Path("outputs") / name)
        command.add_argument("--seed", type=int)
        command.add_argument("--audit", action="store_true", help="check state invariants after each event")
        if name == "run":
            command.add_argument("--trials", type=int)
        else:
            command.add_argument("--sizes", type=int, nargs=3, default=[10_000, 40_000, 160_000])
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        if args.seed is not None:
            config = replace(config, seed=args.seed)
        if args.command == "run":
            if args.trials is not None:
                config = replace(config, trials=args.trials)
            start = perf_counter()
            trials = simulate(config, validate=args.audit)
            summary = export_run(trials, config, args.output, perf_counter() - start)
            print(f"{summary['trials']:,} trials; events={summary['events']:,}; "
                  f"no-event={summary['none_probability']:.6f}; output={args.output}")
        else:
            report = run_stability(config, args.sizes, args.output, audit=args.audit)
            print(f"Stability {'PASS' if report['passed'] else 'FAIL'}: "
                  f"CDF distance={report['final_cdf_distance']:.6f}, bound={report['threshold_95']:.6f}")
            if not report["passed"]:
                return 1
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    return 0
