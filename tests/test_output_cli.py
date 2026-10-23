import contextlib
import csv
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
            self.assertEqual(sum(r['first_attack_time'] == '' for r in raw), summary['zero_attack_trials'])
            self.assertEqual(Config.load(output / 'config.json'), config)
            self.assertEqual(summary['attacked_trials'] + summary['zero_attack_trials'], 100)
            self.assertAlmostEqual(summary['probability_total'], 1)
            self.assertEqual(len(summary['source_sha256']), 64)
            with (output / 'attacks.csv').open() as stream:
                events = list(csv.DictReader(stream))
            self.assertEqual(len(events), summary['total_attacks'])
            self.assertEqual(sum(int(row['attack_count']) for row in raw), len(events))
            self.assertAlmostEqual(summary['density_integral'], summary['mean_attacks'])
            self.assertEqual(summary['mean_attacks'], len(events) / len(raw))

    def test_identical_seed_exports_identical_csv(self):
        config = Config(trials=50)
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / 'a', Path(directory) / 'b'
            for output in (a, b):
                export_run(simulate(config), config, output, 0, plot=False)
            for name in ('trials.csv', 'attacks.csv', 'histogram.csv', 'config.json'):
                self.assertEqual((a / name).read_bytes(), (b / name).read_bytes())

    def test_empty_events_plot_and_cli_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            config = output / 'input.json'
            config.write_text(json.dumps(Config(max_time=5).to_dict()))
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(['run', '--config', str(config), '--trials', '3', '--seed', '12',
                             '--output', str(output / 'run'), '--audit'])
            summary = json.loads((output / 'run/summary.json').read_text())
            self.assertEqual(code, 2)
            self.assertEqual(summary['trials'], 3)
            self.assertEqual(summary['seed'], 12)
            self.assertIsNone(summary['none_probability'])
            self.assertEqual(summary['incomplete_trials'], 3)
            self.assertGreater((output / 'run/density.png').stat().st_size, 1000)
            self.assertTrue((output / 'run/density.svg').exists())
            self.assertGreater((output / 'run/attack_density.png').stat().st_size, 1000)
            self.assertTrue((output / 'run/attack_density.svg').exists())
            self.assertTrue((output / 'run/interval_probability.png').exists())

    def test_small_stability_report_and_invalid_sizes(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            report = run_stability(Config(), [10, 20, 40], Path(directory), audit=True, plot=False, bootstrap_repetitions=20)
            self.assertIsInstance(report['passed'], bool)
            self.assertEqual([r['seed'] for r in report['runs']], [42, 43, 44])
            self.assertGreaterEqual(report['distance'], 0)
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

    def test_guard_prevents_stability_pass_and_exports_reason(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            report = run_stability(Config(max_time=5), [2, 3, 4], Path(directory), plot=False)
            self.assertFalse(report['passed'])
            self.assertIsNone(report['distance'])
            self.assertEqual(report['incomplete_trials'], 2)
            self.assertTrue((Path(directory) / 'stability.json').exists())

    def test_mixed_export_keeps_incomplete_raw_events_but_excludes_primary(self):
        from invis_attack_sim.results import Attack, Trial
        c = Config(trials=3)
        trials = [Trial(0, (Attack(1, 1),), 10, 'game_end'),
                  Trial(1, (), 5, 'eliminated'),
                  Trial(2, (Attack(1, 2), Attack(2, 3)), 5, 'event_guard')]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            summary = export_run(trials, c, output, 0, plot=False)
            self.assertEqual(summary['complete_trials'], 2)
            self.assertEqual(summary['zero_attack_trials'], 1)
            self.assertEqual(summary['total_attacks'], 1)
            self.assertEqual(summary['attempted_total_attacks'], 3)
            self.assertEqual(summary['mean_attacks'], .5)
            with (output / 'attacks.csv').open() as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 3)


if __name__ == '__main__':
    unittest.main()
