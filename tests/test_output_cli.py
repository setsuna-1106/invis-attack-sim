import contextlib
import csv
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest

from invis_attack_sim.cli import main, run_stability
from invis_attack_sim.config import Config
from invis_attack_sim.engine import simulate
from invis_attack_sim.output import export_run


class OutputCliTests(unittest.TestCase):
    def test_export_preserves_raw_data_and_normalizes(self):
        config = Config(trials=100)
        trials = simulate(config)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            summary = export_run(trials, config, output, 1, plot=False)
            with (output / 'trials.csv').open() as stream:
                raw = list(csv.DictReader(stream))
            self.assertEqual(len(raw), 100)
            self.assertEqual(sum(r['event_time'] == '' for r in raw), summary['no_events'])
            self.assertEqual(Config.load(output / 'config.json'), config)
            self.assertEqual(summary['events'] + summary['no_events'], 100)
            self.assertAlmostEqual(summary['probability_total'], 1)
            self.assertEqual(len(summary['source_sha256']), 64)

    def test_identical_seed_exports_identical_csv(self):
        config = Config(trials=50)
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / 'a', Path(directory) / 'b'
            for output in (a, b):
                export_run(simulate(config), config, output, 0, plot=False)
            for name in ('trials.csv', 'histogram.csv', 'config.json'):
                self.assertEqual((a / name).read_bytes(), (b / name).read_bytes())

    def test_empty_events_plot_and_cli_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            config = output / 'input.json'
            config.write_text(json.dumps(Config(horizon=5).to_dict()))
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(['run', '--config', str(config), '--trials', '3', '--seed', '12',
                             '--output', str(output / 'run'), '--audit'])
            summary = json.loads((output / 'run/summary.json').read_text())
            self.assertEqual(code, 0)
            self.assertEqual(summary['trials'], 3)
            self.assertEqual(summary['seed'], 12)
            self.assertEqual(summary['none_probability'], 1)
            self.assertGreater((output / 'run/density.png').stat().st_size, 1000)
            self.assertTrue((output / 'run/density.svg').exists())

    def test_small_stability_report_and_invalid_sizes(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            report = run_stability(Config(horizon=5), [10, 20, 40], Path(directory), audit=True, plot=False)
            self.assertTrue(report['passed'])
            self.assertEqual([r['seed'] for r in report['runs']], [42, 43, 44])
            self.assertEqual(report['final_cdf_distance'], 0)
            self.assertTrue(report['state_audits'])
            with self.assertRaises(ValueError):
                run_stability(Config(), [10, 10, 40], Path(directory), plot=False)

    def test_config_json_rejects_unknown_keys_and_non_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.json'
            for value in ({'unknown': 1}, []):
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    Config.load(path)


if __name__ == '__main__':
    unittest.main()
